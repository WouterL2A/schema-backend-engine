# conversion/schema_converter.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# -----------------------------
# JSON Pointer helpers / merging
# -----------------------------
def _deep_get(doc: dict, pointer: str) -> Optional[dict]:
    if not pointer or not pointer.startswith("#/"):
        return None
    parts = pointer[2:].split("/")
    cur: Any = doc
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    if isinstance(cur, dict):
        return cur
    return None

def _merge_allOf(prop: dict) -> dict:
    base = dict(prop)
    all_of = base.pop("allOf", [])
    merged = {}
    for item in all_of:
        merged.update(item if isinstance(item, dict) else {})
    merged.update(base)
    return merged

def _extract_scalar_constraints(prop: dict, full_doc: dict) -> dict:
    """
    Resolve $ref (if it points to a scalar schema) and flatten allOf.
    Keep only scalar-ish constraints we need for column mapping.
    """
    effective = dict(prop)

    # Inline $ref (merge, but we'll still look at the original $ref outside for FK detection)
    ref = effective.get("$ref")
    if isinstance(ref, str):
        ref_dict = _deep_get(full_doc, ref)
        if isinstance(ref_dict, dict):
            tmp = dict(ref_dict)
            tmp.update({k: v for k, v in effective.items() if k != "$ref"})
            effective = tmp

    # Flatten allOf (including referenced blocks)
    if "allOf" in effective:
        expanded_allof = []
        for item in effective["allOf"]:
            if isinstance(item, dict) and "$ref" in item:
                resolved = _deep_get(full_doc, item["$ref"])
                if isinstance(resolved, dict):
                    expanded_allof.append(resolved)
                else:
                    expanded_allof.append(item)
            else:
                expanded_allof.append(item)
        eff = dict(effective)
        eff["allOf"] = expanded_allof
        effective = _merge_allOf(eff)

    keep = {
        "type", "format", "maxLength", "minLength", "default", "enum",
        "x-unique", "x-refTable", "x-refColumn", "x-relationshipName"
    }
    return {k: v for k, v in effective.items() if k in keep}

# -----------------------------
# Mapping helpers
# -----------------------------
def _map_type_to_column(constraints: dict) -> tuple[str, int | None]:
    typ = constraints.get("type")
    fmt = constraints.get("format")
    max_len = constraints.get("maxLength")

    if fmt == "uuid":
        return "UUID", None
    if fmt == "date-time":
        return "TIMESTAMP", None
    if fmt == "date":
        return "DATE", None
    if fmt == "email":
        return "VARCHAR", int(max_len) if isinstance(max_len, int) else 255

    if typ == "string":
        return "VARCHAR", int(max_len) if isinstance(max_len, int) else 255
    if typ == "integer":
        return "INTEGER", None
    if typ == "number":
        return "FLOAT", None
    if typ == "boolean":
        return "BOOLEAN", None

    # Fallback, includes object/array when stored inline
    return "TEXT", None

# ---- core-mode coercion (optional) ----
ENGINE_ALLOWED = {"UUID", "VARCHAR", "INTEGER", "TIMESTAMP"}

def _coerce_for_engine(dt: str, length: Optional[int]) -> tuple[str, Optional[int], Optional[str]]:
    """
    Force data types into the engine's core set when needed.
    Returns (coerced_type, coerced_length, note)
    """
    if dt in ENGINE_ALLOWED:
        return dt, length, None

    if dt == "BOOLEAN":
        return "INTEGER", length, "coerced BOOLEAN → INTEGER (store 0/1)"
    if dt == "DATE":
        return "TIMESTAMP", length, "coerced DATE → TIMESTAMP"
    if dt == "FLOAT":
        return "VARCHAR", 64, "coerced FLOAT → VARCHAR(64)"
    if dt == "TEXT":
        return "VARCHAR", max(length or 2048, 2048), "coerced TEXT → VARCHAR(2048)"
    # catch-all
    return "VARCHAR", length or 255, f"coerced {dt} → VARCHAR({length or 255})"

def _normalize_default(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lower() in {"now()", "now"}:
        return "now"
    return value

def _def_name_from_ref(ref: str) -> Optional[str]:
    # "#/definitions/users" -> "users"
    if not isinstance(ref, str):
        return None
    prefix = "#/definitions/"
    if ref.startswith(prefix):
        return ref[len(prefix):]
    return None

def _derive_relationship_name(col_name: str, ref_table: str) -> str:
    # Prefer foo_id -> foo
    if col_name.endswith("_id") and len(col_name) > 3:
        return col_name[:-3]
    # Naive singularization (roles -> role, users -> user)
    if ref_table.endswith("s") and len(ref_table) > 1:
        return ref_table[:-1]
    return ref_table

# -----------------------------
# (Optional) FK pointer normalization helpers
# -----------------------------
def _parse_ref_pointer(ptr: str) -> tuple[Optional[str], Optional[str]]:
    """
    Accepts forms like:
      "#/definitions/users"
      "#/definitions/users/properties/id"
      "definitions/users/properties/id"
      "users/properties/id"
      "definitions.users.properties.id"
    Returns (table, column?) where column may be None.
    """
    if not isinstance(ptr, str):
        return None, None
    s = ptr.strip()
    if not s:
        return None, None
    # unify separators
    s = s.lstrip("#/").replace(".", "/")
    parts = [p for p in s.split("/") if p]
    # trim up to 'definitions' if present
    if "definitions" in parts:
        parts = parts[parts.index("definitions") + 1 :]
    table = parts[0] if parts else None
    column = None
    if "properties" in parts:
        i = parts.index("properties")
        if i + 1 < len(parts):
            column = parts[i + 1]
    return table, column

def _normalize_fk_hints(x_ref_table: Optional[str], x_ref_column: Optional[str], raw_ref: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Normalize FK targets from any combination of x-refTable/x-refColumn/$ref.

    Returns (fk_table, fk_column, note) where note is a human-friendly log message if normalization occurred.
    """
    note = None
    fk_table: Optional[str] = None
    fk_column: Optional[str] = None

    # 1) Prefer explicit x-refTable / x-refColumn
    if x_ref_table:
        t1, c1 = _parse_ref_pointer(x_ref_table)
        if t1:
            if t1 != x_ref_table:
                note = f"normalized x-refTable '{x_ref_table}' → '{t1}'"
            fk_table = t1
        else:
            fk_table = x_ref_table  # assume it's already a plain name

        if x_ref_column:
            t2, c2 = _parse_ref_pointer(x_ref_column)
            if c2:
                fk_column = c2
                if x_ref_column != c2:
                    note = (note + "; " if note else "") + f"normalized x-refColumn '{x_ref_column}' → '{c2}'"
            else:
                if any(sep in x_ref_column for sep in ("/", ".", "#")):
                    fk_column = x_ref_column.split("/")[-1].split(".")[-1]
                    if x_ref_column != fk_column:
                        note = (note + "; " if note else "") + f"normalized x-refColumn '{x_ref_column}' → '{fk_column}'"
                else:
                    fk_column = x_ref_column
        elif c1:
            fk_column = c1  # column embedded in x-refTable pointer

    # 2) Else, infer from $ref
    if fk_table is None and isinstance(raw_ref, str):
        t3, c3 = _parse_ref_pointer(raw_ref)
        if t3:
            fk_table = t3
            fk_column = c3 or fk_column

    # Default column
    if fk_table and not fk_column:
        fk_column = "id"

    return fk_table, fk_column, note

def _infer_fk_datatype_from_target(
    full_doc: dict, target_table: str, target_col: str = "id"
) -> tuple[str, int | None]:
    """
    Look up the target table's target_col schema (e.g., users.properties.id)
    and map it to a concrete meta dataType.
    Fallback to UUID if unknown.
    """
    defs = (full_doc or {}).get("definitions") or {}
    tdef = defs.get(target_table) or {}
    props = tdef.get("properties") or {}
    col_schema = props.get(target_col)
    if isinstance(col_schema, dict):
        constraints = _extract_scalar_constraints(col_schema, full_doc=full_doc)
        return _map_type_to_column(constraints)
    # Fallback that matches your example meta
    return "UUID", None

# -----------------------------
# Main conversion
# -----------------------------
def convert_draft7_entities_to_meta(
    draft: dict,
    schema_uri_for_output: str = "schema_definitions/modelSchema.json",
    verbose: bool = True,
    type_mode: str = "core",      # 'core' (UUID/VARCHAR/INTEGER/TIMESTAMP) or 'full' (real types)
    fk_normalize: bool = False,   # OFF by default to avoid changing API contract
) -> dict:
    definitions = draft.get("definitions") or {}
    tables_meta: List[dict] = []

    # Include ALL definitions: root.properties first (for ordering), then any remaining defs
    root_props = list((draft.get("properties") or {}).keys())
    defs_keys = list(definitions.keys())
    ordering = root_props + [k for k in defs_keys if k not in root_props]

    if verbose:
        print(f"[schema-converter] Found {len(ordering)} entities (properties-listed: {len(root_props)}, additional: {len(ordering)-len(root_props)})")

    for table_name in ordering:
        entity = definitions.get(table_name)
        if not isinstance(entity, dict):
            if verbose:
                print(f"  ! Skipping '{table_name}' (not an object schema)")
            continue

        props: dict = entity.get("properties") or {}
        required: List[str] = entity.get("required") or []
        explicit_pk: List[str] = entity.get("x-primaryKey") or []
        inferred_pk: List[str] = ["id"] if not explicit_pk and "id" in props else []
        pk: List[str] = explicit_pk or inferred_pk

        columns: List[dict] = []
        foreign_keys: List[dict] = []
        fk_count = 0

        if verbose:
            origin = "explicit x-primaryKey" if explicit_pk else ("inferred ['id']" if inferred_pk else "none")
            print(f"→ Entity: {table_name}  (PK: {pk if pk else '—'}, source: {origin})")

        for col_name, prop in props.items():
            # Skip inline object/array unless explicitly modeling as FK via x-ref* or $ref
            prop_type = prop.get("type")
            if prop_type in {"array", "object"} and not any(k in prop for k in ("x-refTable", "x-refColumn", "$ref")):
                if verbose:
                    print(f"    • {col_name}: skipped (type {prop_type} without FK hints)")
                continue

            constraints = _extract_scalar_constraints(prop, full_doc=draft)

            # Detect FK source (normalized only if flag set)
            raw_ref = prop.get("$ref")
            if fk_normalize:
                fk_table, fk_column, norm_note = _normalize_fk_hints(
                    constraints.get("x-refTable"),
                    constraints.get("x-refColumn"),
                    raw_ref,
                )
            else:
                fk_table = constraints.get("x-refTable")
                fk_column = constraints.get("x-refColumn") or None
                # fallback to simple #/definitions/<name>
                if fk_table is None:
                    ref_def_name = _def_name_from_ref(raw_ref) if isinstance(raw_ref, str) else None
                    if ref_def_name:
                        fk_table = ref_def_name
                        if fk_column is None:
                            fk_column = "id"
                norm_note = None

            # Map data type for the column
            if fk_table:
                data_type, length = _infer_fk_datatype_from_target(draft, fk_table, fk_column or "id")
            else:
                data_type, length = _map_type_to_column(constraints)

            # Keep real types in 'full' mode; coerce only in 'core' mode
            note = None
            if type_mode == "core":
                data_type, length, note = _coerce_for_engine(data_type, length)

            # Build column meta
            is_pk = col_name in pk
            not_null = (col_name in required) or is_pk
            is_unique = bool(constraints.get("x-unique", False))
            default_val = constraints.get("default")
            default_val = _normalize_default(default_val) if default_val is not None else None

            col_meta: Dict[str, Any] = {
                "columnName": col_name,
                "dataType": data_type,
            }
            if length is not None and data_type == "VARCHAR":
                col_meta["length"] = length
            if not not_null:
                col_meta["isNullable"] = True
            if is_unique:
                col_meta["isUnique"] = True
            if default_val is not None:
                col_meta["defaultValue"] = default_val

            columns.append(col_meta)

            # Logging for column
            if verbose:
                flags = []
                if is_pk: flags.append("PK")
                flags.append("NOT NULL" if not_null else "NULL")
                if is_unique: flags.append("UNIQUE")
                if default_val is not None: flags.append(f"DEFAULT={default_val}")
                lens = f"({length})" if (length is not None and data_type == "VARCHAR") else ""
                msg = f"    • {col_name}: {data_type}{lens}  [{', '.join(flags)}]"
                if note:
                    msg += f"  [{note}]"
                if norm_note:
                    msg += f"  [{norm_note}]"
                print(msg)

            # Build FK meta if applicable
            if fk_table:
                rel_name = constraints.get("x-relationshipName")
                rel = rel_name or _derive_relationship_name(col_name, fk_table)
                fk_entry: Dict[str, Any] = {
                    "columnName": col_name,
                    "referencedTable": fk_table,
                    "referencedColumn": (fk_column or "id"),
                    "relationshipName": rel,
                }
                foreign_keys.append(fk_entry)
                fk_count += 1
                if verbose:
                    print(f"       ↳ FK {col_name} → {fk_table}.{fk_column or 'id'}  (rel: {rel})")

        table_meta: Dict[str, Any] = {
            "tableName": table_name,
            "columns": columns,
        }
        if pk:
            table_meta["primaryKey"] = pk
        if foreign_keys:
            table_meta["foreignKeys"] = foreign_keys

        if verbose:
            print(f"   summary: {len(columns)} columns, {fk_count} foreign keys\n")

        tables_meta.append(table_meta)

    return {"$schema": schema_uri_for_output, "tables": tables_meta}

# -----------------------------
# CLI
# -----------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Convert Draft-07 entities schema to generator meta format.")
    ap.add_argument("input", help="Path to Draft-07 JSON schema-of-entities")
    ap.add_argument("-o", "--output", help="Path to write meta schema json", default="schema.meta.json")
    ap.add_argument("--schema-uri", help="Value to set in output $schema", default="schema_definitions/modelSchema.json")
    ap.add_argument(
        "--types",
        choices=["core", "full"],
        default="core",
        help="Type set for meta output: 'core' (UUID/VARCHAR/INTEGER/TIMESTAMP) or 'full' (BOOLEAN/DATE/DECIMAL/etc.)"
    )
    ap.add_argument(
        "--fk-normalize",
        action="store_true",
        help="Normalize x-refTable/x-refColumn/$ref pointers to clean table/column names (opt-in)"
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="Suppress per-entity/field messages")
    args = ap.parse_args()

    src = json.loads(Path(args.input).read_text(encoding="utf-8"))
    meta = convert_draft7_entities_to_meta(
        src,
        schema_uri_for_output=args.schema_uri,
        verbose=not args.quiet,
        type_mode=args.types,
        fk_normalize=args.fk_normalize,
    )
    Path(args.output).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
