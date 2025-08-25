# engine/migrate_additive.py
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ---- small plan model --------------------------------------------------------

@dataclass
class AddColumn:
    table: str
    column: str
    type_sql: str  # raw SQL type

@dataclass
class AddFK:
    table: str
    column: str
    ref_table: str
    ref_column: str

@dataclass
class Plan:
    add_columns: List[AddColumn]
    add_fks: List[AddFK]

# ---- helpers ----------------------------------------------------------------

def _sql_type_for(meta_dtype: str, length: Optional[int], precision: Optional[int],
                  scale: Optional[int], dialect_name: str) -> str:
    dt = (meta_dtype or "").upper()
    # Simple, conservative mapping that works across sqlite/pg
    if dt == "UUID":
        return "VARCHAR(36)" if dialect_name == "sqlite" else "UUID"
    if dt == "VARCHAR":
        return f"VARCHAR({length or 255})"
    if dt in ("TEXT",):
        return "TEXT"
    if dt in ("INTEGER", "BIGINT"):
        return dt
    if dt == "DECIMAL":
        p = precision or 18
        s = scale or 6
        return f"DECIMAL({p},{s})"
    if dt == "FLOAT":
        return "FLOAT"
    if dt == "BOOLEAN":
        return "BOOLEAN"
    if dt == "DATE":
        return "DATE"
    if dt == "TIMESTAMP":
        # keep generic; engines will map it
        return "TIMESTAMP"
    if dt == "JSON":
        # sqlite has no JSON type; use TEXT
        return "JSONB" if dialect_name in ("postgresql", "postgres") else "TEXT"
    if dt == "BLOB":
        return "BLOB"
    return "TEXT"

def _existing_columns_map(engine: Engine) -> Dict[str, Dict[str, str]]:
    insp = inspect(engine)
    result: Dict[str, Dict[str, str]] = {}
    for t in insp.get_table_names():
        cols = {}
        for c in insp.get_columns(t):
            cols[c["name"]] = (str(c.get("type")) or "").upper()
        result[t] = cols
    return result

# ---- planner ----------------------------------------------------------------

def _build_plan(engine: Engine, meta, dialect: str) -> Plan:
    """
    meta: engine.meta_models.ModelMeta (pydantic) – already validated in main.py
    """
    dialect_name = engine.dialect.name
    existing = _existing_columns_map(engine)

    add_cols: List[AddColumn] = []
    add_fks: List[AddFK] = []

    for t in meta.tables:
        tname = t.tableName
        current_cols = existing.get(tname, {})
        # columns
        for col in t.columns:
            if col.columnName not in current_cols:
                type_sql = _sql_type_for(
                    getattr(col.dataType, "value", str(col.dataType)),
                    getattr(col, "length", None),
                    getattr(col, "precision", None),
                    getattr(col, "scale", None),
                    dialect_name,
                )
                add_cols.append(AddColumn(tname, col.columnName, type_sql))

        # FKs
        for fk in (t.foreignKeys or []):
            # We cannot reliably introspect “missing fk constraints” on sqlite.
            # For non-sqlite, we’ll try to add if the column exists (or is planned).
            add_fks.append(AddFK(tname, fk.columnName, fk.referencedTable, fk.referencedColumn))

    # Keep only FK where the column will exist (already or planned)
    existing_after_add = {t: set(cols.keys()) for t, cols in _existing_columns_map(engine).items()}
    for ac in add_cols:
        existing_after_add.setdefault(ac.table, set()).add(ac.column)
    add_fks = [fk for fk in add_fks if fk.column in existing_after_add.get(fk.table, set())]

    return Plan(add_cols, add_fks)

# ---- applier ----------------------------------------------------------------

def apply_additive_changes(engine: Engine, plan: Plan) -> None:
    """
    SQLAlchemy 2.x–compatible apply:
      - uses engine.begin() and connection.exec_driver_sql()
      - adds columns
      - adds FKs where supported (skips on sqlite with a warning)
    """
    dialect = engine.dialect.name

    if not plan.add_columns and not plan.add_fks:
        logger.info("Nothing to apply.")
        return

    logger.warning("=== APPLY ADDITIVE ===")
    for ac in plan.add_columns:
        logger.warning("ADD COLUMN %s.%s %s", ac.table, ac.column, ac.type_sql)
    for fk in plan.add_fks:
        logger.warning("ADD FK %s.%s -> %s.%s", fk.table, fk.column, fk.ref_table, fk.ref_column)

    with engine.begin() as conn:
        # Add columns
        for ac in plan.add_columns:
            sql = f'ALTER TABLE "{ac.table}" ADD COLUMN "{ac.column}" {ac.type_sql}'
            try:
                conn.exec_driver_sql(sql)
            except Exception as e:
                # idempotency / already exists, or unsupported default/constraint on add
                logger.error("ADD COLUMN failed (continuing): %s ; error=%s", sql, e)

        # Add FKs (skip on sqlite)
        if dialect == "sqlite":
            for fk in plan.add_fks:
                logger.warning(
                    "SQLite cannot ALTER TABLE to add FK post-hoc. Skipping FK %s.%s -> %s.%s",
                    fk.table, fk.column, fk.ref_table, fk.ref_column
                )
        else:
            for fk in plan.add_fks:
                cname = f'fk_{fk.table}_{fk.column}_{fk.ref_table}_{fk.ref_column}'
                sql = (
                    f'ALTER TABLE "{fk.table}" '
                    f'ADD CONSTRAINT "{cname}" FOREIGN KEY ("{fk.column}") '
                    f'REFERENCES "{fk.ref_table}"("{fk.ref_column}")'
                )
                try:
                    conn.exec_driver_sql(sql)
                except Exception as e:
                    logger.error("ADD FK failed (continuing): %s ; error=%s", sql, e)

# ---- public entry ------------------------------------------------------------

def plan_and_apply_additive(engine: Engine, meta, dialect: str = "generic", apply: bool = False) -> None:
    plan = _build_plan(engine, meta, dialect)

    # Print plan (always)
    print("\n=== META ADDITIVE PLAN ===")
    if plan.add_columns:
        print("Columns to add:")
        for ac in plan.add_columns:
            print(f"  - {ac.table}.{ac.column} : {ac.type_sql}")
    else:
        print("Columns to add: (none)")
    if plan.add_fks:
        print("FKs to add:")
        for fk in plan.add_fks:
            print(f"  - {fk.table}.{fk.column} -> {fk.ref_table}.{fk.ref_column}")
    else:
        print("FKs to add: (none)")
    print("")

    if apply:
        apply_additive_changes(engine, plan)
    else:
        # Signal plan-only to the caller (engine.main logs & exits)
        logger.warning("PLAN ONLY. No writes will occur.")
