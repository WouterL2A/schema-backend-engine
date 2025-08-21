
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    effective = dict(prop)
    ref = effective.get("$ref")
    if isinstance(ref, str):
        ref_dict = _deep_get(full_doc, ref)
        if isinstance(ref_dict, dict):
            tmp = dict(ref_dict)
            tmp.update({k: v for k, v in effective.items() if k != "$ref"})
            effective = tmp
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
        "type", "format", "maxLength", "minLength", "default",
        "x-unique", "x-refTable", "x-refColumn", "x-relationshipName"
    }
    return {k: v for k, v in effective.items() if k in keep}

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

    return "TEXT", None

def _normalize_default(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lower() in {"now()", "now"}:
        return "now"
    return value

def convert_draft7_entities_to_meta(draft: dict, schema_uri_for_output: str = "schema_definitions/modelSchema.json") -> dict:
    definitions = draft.get("definitions") or {}
    tables_meta: List[dict] = []

    ordering = list((draft.get("properties") or {}).keys()) or list(definitions.keys())

    for table_name in ordering:
        entity = definitions.get(table_name)
        if not isinstance(entity, dict):
            continue

        props: dict = entity.get("properties") or {}
        required: List[str] = entity.get("required") or []
        pk: List[str] = entity.get("x-primaryKey") or []

        columns: List[dict] = []
        foreign_keys: List[dict] = []

        for col_name, prop in props.items():
            prop_type = prop.get("type")
            if prop_type in {"array", "object"}:
                if not any(k in prop for k in ("x-refTable", "x-refColumn")):
                    continue

            constraints = _extract_scalar_constraints(prop, full_doc=draft)
            data_type, length = _map_type_to_column(constraints)

            col_meta = {
                "columnName": col_name,
                "dataType": data_type,
            }
            if length is not None and data_type == "VARCHAR":
                col_meta["length"] = length

            if col_name not in required:
                col_meta["isNullable"] = True

            if "x-unique" in constraints:
                col_meta["isUnique"] = bool(constraints["x-unique"])

            if "default" in constraints:
                col_meta["defaultValue"] = _normalize_default(constraints["default"])

            columns.append(col_meta)

            if "x-refTable" in constraints and "x-refColumn" in constraints:
                fk = {
                    "columnName": col_name,
                    "referencedTable": constraints["x-refTable"],
                    "referencedColumn": constraints["x-refColumn"],
                }
                rel = constraints.get("x-relationshipName")
                if isinstance(rel, str) and rel:
                    fk["relationshipName"] = rel
                else:
                    if col_name.endswith("_id"):
                        fk["relationshipName"] = col_name[:-3]
                foreign_keys.append(fk)

        table_meta = {
            "tableName": table_name,
            "columns": columns,
        }
        if pk:
            table_meta["primaryKey"] = pk
        if foreign_keys:
            table_meta["foreignKeys"] = foreign_keys

        tables_meta.append(table_meta)

    return {"$schema": schema_uri_for_output, "tables": tables_meta}

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Convert Draft-07 entities schema to generator meta format.")
    ap.add_argument("input", help="Path to Draft-07 JSON schema-of-entities")
    ap.add_argument("-o", "--output", help="Path to write meta schema json", default="schema.meta.json")
    ap.add_argument("--schema-uri", help="Value to set in output $schema", default="schema_definitions/modelSchema.json")
    args = ap.parse_args()

    src = json.loads(Path(args.input).read_text(encoding="utf-8"))
    meta = convert_draft7_entities_to_meta(src, schema_uri_for_output=args.schema_uri)
    Path(args.output).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
