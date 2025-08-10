# generate/models.py
from datetime import datetime
import uuid
from typing import Dict, Any

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import relationship, declarative_base
from generate.loader import load_schema

Base = declarative_base()

def map_type(data_type: str, length: int | None = None):
    """
    Map spec dataType -> SQLAlchemy type.
    UUID  -> String(36)  (portable across SQLite/MSSQL/MySQL/Postgres)
    VARCHAR -> String(length or default)
    INTEGER -> Integer
    TIMESTAMP -> DateTime
    """
    if data_type == "UUID":
        return String(36)
    if data_type == "VARCHAR":
        return String(length) if length else String
    if data_type == "INTEGER":
        return Integer
    if data_type == "TIMESTAMP":
        return DateTime
    raise ValueError(f"Unsupported dataType: {data_type}")

def generate_models() -> Dict[str, Any]:
    spec = load_schema()
    models: Dict[str, Any] = {}

    # Pre-index FKs by table for quick lookup: {tableName: {columnName: (refTable, refCol, relName)}}
    fk_index: Dict[str, Dict[str, tuple[str, str, str | None]]] = {}
    for t in spec["tables"]:
        fks = {}
        for fk in t.get("foreignKeys", []) or []:
            fks[fk["columnName"]] = (fk["referencedTable"], fk["referencedColumn"], fk.get("relationshipName"))
        fk_index[t["tableName"]] = fks

    # First pass: define models + columns
    for t in spec["tables"]:
        table_name = t["tableName"]
        columns = t["columns"]
        pk_list = set(t.get("primaryKey", []))
        fks_for_table = fk_index.get(table_name, {})

        class_attrs: Dict[str, Any] = {
            "__tablename__": table_name,
            "__table_args__": {"extend_existing": True},
        }

        # Build Columns
        for col in columns:
            col_name = col["columnName"]
            data_type = col["dataType"]
            length = col.get("length")
            sa_type = map_type(data_type, length)

            kwargs: Dict[str, Any] = {}
            # Primary key (from table.primaryKey list)
            if col_name in pk_list:
                kwargs["primary_key"] = True
                if data_type == "UUID":
                    kwargs["default"] = lambda: str(uuid.uuid4())
            # Nullability (default True unless explicitly false)
            if col.get("isNullable") is False:
                kwargs["nullable"] = False
            # Uniqueness
            if col.get("isUnique"):
                kwargs["unique"] = True
            # Defaults
            if col.get("defaultValue") == "now" and data_type == "TIMESTAMP":
                kwargs["default"] = datetime.utcnow

            # Foreign key?
            if col_name in fks_for_table:
                ref_table, ref_col, _rel = fks_for_table[col_name]
                class_attrs[col_name] = Column(sa_type, ForeignKey(f"{ref_table}.{ref_col}"), **kwargs)
            else:
                class_attrs[col_name] = Column(sa_type, **kwargs)

        # Create model class
        model_cls = type(table_name.capitalize(), (Base,), class_attrs)
        models[table_name] = model_cls

    # Second pass: add relationships
    # We add a relationship on the child table pointing to parent table
    for t in spec["tables"]:
        table_name = t["tableName"]
        model_cls = models[table_name]
        for fk in t.get("foreignKeys", []) or []:
            ref_table = fk["referencedTable"]
            rel_name = fk.get("relationshipName") or ref_table.rstrip("s")
            setattr(model_cls, rel_name, relationship(models[ref_table]))

    return models
