# engine/schema_guard.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List
from sqlalchemy import inspect as sa_inspect
from engine.meta_models import ModelMeta

@dataclass
class SchemaDiff:
    missing_tables: List[str] = field(default_factory=list)
    missing_columns: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return bool(self.missing_tables or self.missing_columns)

    def format_plan(self) -> str:
        lines: List[str] = []
        if self.missing_tables:
            lines.append("Missing tables:")
            for t in sorted(self.missing_tables):
                lines.append(f"  - {t}")
        if self.missing_columns:
            lines.append("Missing columns:")
            for t, cols in sorted(self.missing_columns.items()):
                for c in sorted(cols):
                    lines.append(f"  - {t}.{c}")
        return "\n".join(lines) if lines else "No schema differences detected."

def diff_schema(engine, meta: ModelMeta) -> SchemaDiff:
    """
    Compare actual DB schema with ModelMeta (additive-only check):
      - tables in meta that don't exist in DB
      - columns in meta missing from DB tables
    (We don't enforce types/FKs here to keep this safe & additive.)
    """
    insp = sa_inspect(engine)
    existing_tables = set(insp.get_table_names())
    diff = SchemaDiff()

    def db_columns(table: str) -> List[str]:
        try:
            return [c["name"] for c in insp.get_columns(table)]
        except Exception:
            return []

    for t in meta.tables:
        tname = t.tableName
        if tname not in existing_tables:
            diff.missing_tables.append(tname)
            continue
        meta_cols = {c.columnName for c in t.columns}
        db_cols = set(db_columns(tname))
        missing = sorted(meta_cols - db_cols)
        if missing:
            diff.missing_columns[tname] = missing

    return diff
