# engine/ddl_builder.py
from __future__ import annotations
from typing import Dict, Any, Tuple, Optional, List
from sqlalchemy import Column, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase
from engine.meta_models import ModelMeta, Table, Column as MetaCol
from engine.type_mapping import sqlalchemy_type

class Base(DeclarativeBase):
    pass

def _default_kwargs(meta_col: MetaCol, data_type: str) -> dict:
    kwargs = {
        "nullable": bool(meta_col.isNullable) if meta_col.isNullable is not None else False,
        "unique": bool(meta_col.isUnique) if meta_col.isUnique is not None else False,
    }

    dv = meta_col.defaultValue

    # Normalize common timestamp/date "now" spellings
    def _is_now(val: object) -> bool:
        if not isinstance(val, str):
            return False
        v = val.strip().lower()
        return v in {"now", "now()", "current_timestamp", "current_timestamp()"}

    if _is_now(dv):
        if data_type.upper() == "TIMESTAMP":
            kwargs["server_default"] = func.now()
        elif data_type.upper() == "DATE":
            kwargs["server_default"] = func.current_date()
    elif dv is not None:
        # For literal defaults, use SQLAlchemy's client-side default
        kwargs["default"] = dv

    return kwargs

def _build_columns_for_table(
    table_meta: Table,
    dialect: str
) -> Tuple[Dict[str, Any], List[Tuple[str, str, str]]]:
    """
    Returns (attrs, fks)
    attrs: dict of class attributes to set (Columns)
    fks: list of tuples (col_name, ref_table, ref_col)
    """
    attrs: Dict[str, Any] = {"__tablename__": table_meta.tableName}
    primary_keys = set(table_meta.primaryKey or [])
    fk_tuples: List[Tuple[str, str, str]] = []

    # Index foreign keys by column for quick lookup
    fk_map = {}
    for fk in (table_meta.foreignKeys or []):
        fk_map[fk.columnName] = (fk.referencedTable, fk.referencedColumn)

    for col in table_meta.columns:
        sa_type = sqlalchemy_type(
            col.dataType.value if hasattr(col.dataType, "value") else str(col.dataType),
            length=col.length,
            precision=col.precision,
            scale=col.scale,
            dialect=dialect,
        )
        is_pk = col.columnName in primary_keys
        fk_ref = fk_map.get(col.columnName)

        kwargs = _default_kwargs(col, col.dataType.value if hasattr(col.dataType, "value") else str(col.dataType))

        if fk_ref:
            ref_table, ref_col = fk_ref
            col_obj = Column(
                col.columnName,
                sa_type,
                ForeignKey(f"{ref_table}.{ref_col}"),
                primary_key=is_pk,
                **kwargs,
            )
            fk_tuples.append((col.columnName, ref_table, ref_col))
        else:
            col_obj = Column(
                col.columnName,
                sa_type,
                primary_key=is_pk,
                **kwargs,
            )

        attrs[col.columnName] = col_obj

    return attrs, fk_tuples

def build_models_from_meta(meta: ModelMeta, dialect: str = "generic") -> Dict[str, type]:
    """
    Dynamically build SQLAlchemy ORM models for each table in meta.
    Returns {tableName: ModelClass}
    """
    models: Dict[str, type] = {}
    for table in meta.tables:
        attrs, _ = _build_columns_for_table(table, dialect=dialect)
        cls_name = "".join(part.capitalize() for part in table.tableName.split("_"))
        model_cls = type(cls_name, (Base,), attrs)
        models[table.tableName] = model_cls
    return models

def create_all_from_meta(engine, meta: ModelMeta, dialect: str = "generic") -> Dict[str, type]:
    models = build_models_from_meta(meta, dialect=dialect)
    Base.metadata.create_all(bind=engine)
    return models
