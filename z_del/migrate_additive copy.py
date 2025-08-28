# engine/migrate_additive.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

from sqlalchemy import inspect, text, Column as SAColumn
from sqlalchemy.engine import Engine
from sqlalchemy.sql.sqltypes import String

from engine.meta_models import ModelMeta, Table, Column as MetaCol
from engine.type_mapping import sqlalchemy_type

@dataclass
class Plan:
    creates: List[str] = field(default_factory=list)                # tables to create (create_all already does this)
    add_columns: List[Tuple[str, SAColumn]] = field(default_factory=list)  # (table, SA Column)
    add_fks: List[Tuple[str, str, str]] = field(default_factory=list)      # (table, col, "reftable.refcol")
    warnings: List[str] = field(default_factory=list)

def _sa_col_from_meta(meta_col: MetaCol, dialect: str) -> SAColumn:
    sa_type = sqlalchemy_type(
        (meta_col.dataType.value if hasattr(meta_col.dataType, "value") else str(meta_col.dataType)),
        length=meta_col.length,
        precision=meta_col.precision,
        scale=meta_col.scale,
        dialect=dialect,
    )
    # When adding columns on an existing table, default to nullable to avoid failing on existing rows.
    # We emit a warning later if IR says NOT NULL.
    return SAColumn(meta_col.columnName, sa_type, nullable=True)

def _compile_type(col: SAColumn, engine: Engine) -> str:
    return col.type.compile(dialect=engine.dialect)

def plan_additive_changes(engine: Engine, meta: ModelMeta, dialect: str) -> Plan:
    insp = inspect(engine)
    plan = Plan()
    db_tables = set(insp.get_table_names())

    for t in meta.tables:
        tname = t.tableName
        if tname not in db_tables:
            plan.creates.append(tname)
            # create_all() will handle new tables; we don't need to duplicate here
            continue

        # Compare columns (add missing)
        db_cols = {c["name"]: c for c in insp.get_columns(tname)}
        for mcol in t.columns:
            if mcol.columnName not in db_cols:
                plan.add_columns.append((tname, _sa_col_from_meta(mcol, dialect)))
            else:
                # Warn if IR says NOT NULL but DB column is nullable
                dbc = db_cols[mcol.columnName]
                want_not_null = (mcol.isNullable is False)
                if want_not_null and dbc.get("nullable", True):
                    plan.warnings.append(
                        f"[{tname}.{mcol.columnName}] DB is NULLABLE but IR is NOT NULL (manual migration recommended)."
                    )

        # Foreign keys (best effort: add if missing).
        try:
            db_fks = insp.get_foreign_keys(tname)
        except NotImplementedError:
            db_fks = []
        existing = {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"])) for fk in db_fks}

        for fk in (t.foreignKeys or []):
            pair = ((fk.columnName,), fk.referencedTable, (fk.referencedColumn,))
            if pair not in existing:
                plan.add_fks.append((tname, fk.columnName, f"{fk.referencedTable}.{fk.referencedColumn}"))

    return plan

def apply_additive_changes(engine: Engine, plan: Plan):
    # Add columns
    for tname, sa_col in plan.add_columns:
        colname = sa_col.name
        t_sqltype = _compile_type(sa_col, engine)
        # NOTE: we add as NULLABLE to avoid failing on existing data; tighten later via a proper migration.
        sql = f'ALTER TABLE "{tname}" ADD COLUMN "{colname}" {t_sqltype};'
        print(f"ADD COLUMN {tname}.{colname} {t_sqltype}")
        engine.execute(text(sql))

    # Add FKs (not supported on SQLite post-create)
    if plan.add_fks:
        if engine.dialect.name == "sqlite":
            print("SQLite: skipping FK additions (requires table rebuild).")
            return
        for tname, col, ref in plan.add_fks:
            ref_table, ref_col = ref.split(".", 1)
            cname = f"fk_{tname}_{col}_{ref_table}_{ref_col}"
            sql = f'ALTER TABLE "{tname}" ADD CONSTRAINT "{cname}" FOREIGN KEY ("{col}") REFERENCES "{ref_table}" ("{ref_col}");'
            print(f"ADD FK {cname} on {tname}({col}) -> {ref}")
            engine.execute(text(sql))

def plan_and_apply_additive(engine: Engine, meta: ModelMeta, dialect: str, apply: bool):
    plan = plan_additive_changes(engine, meta, dialect)
    print("\n=== META ADDITIVE PLAN ===")
    if plan.creates:
        print("New tables (create_all will create):", ", ".join(plan.creates))
    if plan.add_columns:
        print("Columns to add:")
        for tname, col in plan.add_columns:
            print(f"  - {tname}.{col.name} : {col.type}")
    if plan.add_fks:
        print("FKs to add:")
        for tname, col, ref in plan.add_fks:
            print(f"  - {tname}.{col} -> {ref}")
    if plan.warnings:
        print("Warnings:")
        for w in plan.warnings:
            print("  !", w)
    if not (plan.creates or plan.add_columns or plan.add_fks or plan.warnings):
        print("No differences detected.")

    if apply:
        print("\n=== APPLY ADDITIVE ===")
        apply_additive_changes(engine, plan)
        print("Done.")
