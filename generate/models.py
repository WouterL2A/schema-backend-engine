from datetime import datetime
import uuid
from typing import Dict, Any

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    ForeignKey,
    Boolean as SABoolean,
    Text
)
from sqlalchemy.orm import relationship, declarative_base
from generate.loader import load_schema
from sqlalchemy.dialects.sqlite import JSON as SAJSON

Base = declarative_base()


def map_type(data_type: str, length: int | None = None):
    """
    Map spec dataType -> SQLAlchemy type.
    UUID      -> String(36)  (portable)
    VARCHAR   -> String(length or default)
    INTEGER   -> Integer
    TIMESTAMP -> DateTime
    """
    if data_type == "UUID":       return String(36)
    if data_type == "VARCHAR":    return String(length) if length else String
    if data_type == "INTEGER":    return Integer
    if data_type == "TIMESTAMP":  return DateTime
    if data_type == "BOOLEAN":    return SABoolean
    if data_type == "JSON":       return SAJSON
    if data_type == "TEXT":       return Text
    raise ValueError(f"Unsupported dataType: {data_type}")


def _derive_rel_name(fk: Dict[str, Any], fallback_table: str) -> str:
    """
    Choose a stable relationship attribute name.
    Priority:
      1) explicit relationshipName
      2) columnName with trailing '_id' stripped (user_id -> user)
      3) singularized referenced table (naive: rstrip('s'))
    """
    if fk.get("relationshipName"):
        return fk["relationshipName"]
    col = fk.get("columnName", "")
    if col.endswith("_id") and len(col) > 3:
        return col[:-3]
    rt = (fk.get("referencedTable") or fallback_table).rstrip("s")
    return rt or "parent"


def _is_now_default(val: Any, data_type: str) -> bool:
    """Detect 'now'/'now()' style defaults for TIMESTAMP."""
    if data_type != "TIMESTAMP" or val is None:
        return False
    s = str(val).strip().lower()
    return s in {"now", "now()", "current_timestamp", "current_timestamp()"}


def generate_models() -> Dict[str, Any]:
    """
    Build SQLAlchemy models dynamically from the validated instance schema (modelSchema.json shape).
    """
    spec = load_schema()
    models: Dict[str, Any] = {}

    # Index foreign-keys for quick lookup: {tableName: {columnName: fk_dict}}
    fk_index: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for t in spec["tables"]:
        fks_for_table: Dict[str, Dict[str, Any]] = {}
        for fk in (t.get("foreignKeys") or []):
            fks_for_table[fk["columnName"]] = fk
        fk_index[t["tableName"]] = fks_for_table

    # ---------- First pass: define models + columns ----------
    for t in spec["tables"]:
        table_name = t["tableName"]
        columns = t["columns"]
        pk_list = set(t.get("primaryKey", []))
        fks_for_table = fk_index.get(table_name, {})

        class_attrs: Dict[str, Any] = {
            "__tablename__": table_name,
            "__table_args__": {"extend_existing": True},
        }

        for col in columns:
            col_name = col["columnName"]
            data_type = col["dataType"]
            length = col.get("length")
            sa_type = map_type(data_type, length)

            kwargs: Dict[str, Any] = {}

            # Primary key
            if col_name in pk_list:
                kwargs["primary_key"] = True
                if data_type == "UUID":
                    kwargs["default"] = lambda: str(uuid.uuid4())

            # Nullability
            if col.get("isNullable") is False:
                kwargs["nullable"] = False

            # Uniqueness
            if col.get("isUnique"):
                kwargs["unique"] = True

            # Defaults
            default_val = col.get("defaultValue")
            if _is_now_default(default_val, data_type):
                # Python-side default at INSERT time (portable)
                kwargs["default"] = datetime.utcnow
            elif default_val is not None:
                # Literal default (string/number/bool); SQLAlchemy will use it on INSERT
                kwargs["default"] = default_val

            # Foreign key?
            if col_name in fks_for_table:
                ref_table = fks_for_table[col_name]["referencedTable"]
                ref_col = fks_for_table[col_name]["referencedColumn"]
                class_attrs[col_name] = Column(sa_type, ForeignKey(f"{ref_table}.{ref_col}"), **kwargs)
            else:
                class_attrs[col_name] = Column(sa_type, **kwargs)

        # Create model class
        model_cls = type(table_name.capitalize(), (Base,), class_attrs)
        models[table_name] = model_cls

    # ---------- Second pass: add relationships (one per FK) ----------
    for t in spec["tables"]:
        table_name = t["tableName"]
        model_cls = models[table_name]

        # Track names to avoid accidental collisions
        used_rel_names: set[str] = set()

        for fk in (t.get("foreignKeys") or []):
            ref_table = fk["referencedTable"]
            col_name = fk["columnName"]

            # Decide attribute name
            rel_name = _derive_rel_name(fk, ref_table)
            base_rel = rel_name
            i = 2
            while hasattr(model_cls, rel_name) or rel_name in used_rel_names:
                rel_name = f"{base_rel}_{i}"
                i += 1
            used_rel_names.add(rel_name)

            # Resolve the FK column attribute on THIS model
            fk_col_attr = getattr(model_cls, col_name)

            # Build the relationship with explicit foreign_keys to avoid ambiguity
            setattr(
                model_cls,
                rel_name,
                relationship(
                    models[ref_table],
                    foreign_keys=[fk_col_attr],
                    lazy="selectin",   # nice default for list endpoints
                ),
            )

    return models
