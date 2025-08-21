# pyright: reportInvalidTypeForm=false
# engine/routes_meta.py

import logging
from typing import Any, Dict, List, Optional, Tuple, Type
from uuid import UUID
from decimal import Decimal
from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, create_model, Field
from sqlalchemy import select, func, String, Text
from sqlalchemy.orm import Session

from engine.db import get_db
from .routes_base import (
    _strip_server_managed,
    _apply_server_defaults_on_create,
    _apply_server_defaults_on_update,
    _coerce_uuid_attrs_for_sqlite,
    _string_columns,
    _apply_sort,
    _serialize_row,
)

# meta models are optional at import-time
try:
    from engine.meta_models import ModelMeta, Table, Column as MetaCol  # type: ignore
except Exception:
    ModelMeta = Any  # type: ignore
    Table = Any      # type: ignore
    MetaCol = Any    # type: ignore

logger = logging.getLogger(__name__)

def _py_type_for(col: MetaCol):
    dt = (col.dataType.value if hasattr(col.dataType, "value") else str(col.dataType)).upper()
    if dt == "UUID":
        return UUID
    if dt in ("VARCHAR", "TEXT"):
        return str
    if dt in ("INTEGER", "BIGINT"):
        return int
    if dt == "DECIMAL":
        return Decimal
    if dt == "FLOAT":
        return float
    if dt == "BOOLEAN":
        return bool
    if dt == "DATE":
        return date
    if dt == "TIMESTAMP":
        return datetime
    if dt == "JSON":
        return Any
    if dt == "BLOB":
        return bytes
    return Any

def _is_required_meta(col: MetaCol, pk: List[str]) -> bool:
    # server-managed & PK not required in Create
    if col.columnName in (pk or []):
        return False
    if getattr(col, "isNullable", None):
        return False
    if col.defaultValue is not None:
        return False
    if col.columnName in {"id", "created_at", "updated_at", "created_by", "updated_by",
                          "createdAt", "updatedAt", "createdBy", "updatedBy"}:
        return False
    return True

def _make_pydantic_models_from_meta(entity: Table) -> Tuple[Type[BaseModel], Type[BaseModel], Type[BaseModel], Type[BaseModel]]:
    """
    Returns (CreateModel, ReadModel, UpdateModel, ListResponseModel)
    """
    pk = entity.primaryKey or []
    create_fields: Dict[str, tuple] = {}
    read_fields: Dict[str, tuple] = {}
    update_fields: Dict[str, tuple] = {}

    for c in entity.columns:
        py_t = _py_type_for(c)
        name = c.columnName

        # Read: include everything (nullable for schema stability)
        read_fields[name] = (py_t, Field(default=None))

        # Create: exclude server-managed
        if name not in {"id", "created_at", "updated_at", "created_by", "updated_by",
                        "createdAt", "updatedAt", "createdBy", "updatedBy"}:
            if _is_required_meta(c, pk):
                create_fields[name] = (py_t, ...)
            else:
                create_fields[name] = (Optional[py_t], None)

        # Update: exclude server-managed
        if name not in {"id", "created_at", "updated_at", "created_by", "updated_by",
                        "createdAt", "updatedAt", "createdBy", "updatedBy"}:
            update_fields[name] = (Optional[py_t], None)

    base_name = entity.tableName.title().replace("_", "")
    CreateModel = create_model(f"{base_name}Create", __base__=BaseModel, **create_fields)
    ReadModel = create_model(f"{base_name}Read", __base__=BaseModel, **read_fields)
    UpdateModel = create_model(f"{base_name}Update", __base__=BaseModel, **update_fields)
    ListResponseModel = create_model(
        f"{base_name}ListResponse",
        __base__=BaseModel,
        total=(int, ...),
        limit=(int, ...),
        offset=(int, ...),
        items=(List[ReadModel], ...),
    )
    for m in (CreateModel, ReadModel, UpdateModel, ListResponseModel):
        if hasattr(m, "model_rebuild"):
            m.model_rebuild()  # type: ignore[attr-defined]
    return CreateModel, ReadModel, UpdateModel, ListResponseModel

def build_crud_router(entity: Table, model, meta: ModelMeta) -> APIRouter:
    """
    Meta-driven router for a single entity. This is what engine.main imports.
    """
    router = APIRouter(prefix=f"/{entity.tableName}", tags=[entity.tableName])
    pk = entity.primaryKey or []
    if len(pk) != 1:
        return router

    CreateModel, ReadModel, UpdateModel, ListResponseModel = _make_pydantic_models_from_meta(entity)

    @router.get("/", response_model=ListResponseModel)
    def list_items(
        db: Session = Depends(get_db),
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0),
        sort: Optional[str] = Query(None, description="e.g. -created_at,name"),
        q: Optional[str] = Query(None, description="basic text search across string columns"),
    ):
        stmt = select(model)
        if q:
            ors = [c.ilike(f"%{q}%") for c in _string_columns(model)]
            if ors:
                from sqlalchemy import or_ as _or
                stmt = stmt.where(_or(*ors))
        order_by = _apply_sort(model, sort)
        if order_by:
            stmt = stmt.order_by(*order_by)

        total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
        items = [_serialize_row(r) for r in rows]
        return {"total": total, "limit": limit, "offset": offset, "items": items}

    @router.get("/{item_id}", response_model=ReadModel)
    def get_item(item_id: str, db: Session = Depends(get_db)):
        row = db.get(model, item_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"{entity.tableName} not found")
        return _serialize_row(row)

    @router.post("/", response_model=ReadModel, status_code=201)
    def create_item(payload: CreateModel = Body(...), db: Session = Depends(get_db)):  # type: ignore[reportInvalidTypeForm]
        data = _strip_server_managed(payload.model_dump(exclude_unset=True), pk)
        obj = model(**data)
        _apply_server_defaults_on_create(obj)
        _coerce_uuid_attrs_for_sqlite(obj, db)
        db.add(obj)
        db.flush()
        db.refresh(obj)
        db.commit()
        return _serialize_row(obj)

    # Register BOTH verbs on one handler to avoid 405s
    @router.api_route("/{item_id}", methods=["PATCH", "PUT"], response_model=ReadModel)
    def update_or_replace_item(item_id: str, payload: UpdateModel = Body(...), db: Session = Depends(get_db)):  # type: ignore[reportInvalidTypeForm]
        obj = db.get(model, item_id)
        if not obj:
            raise HTTPException(status_code=404, detail=f"{entity.tableName} not found")
        data = _strip_server_managed(payload.model_dump(exclude_unset=True), pk)
        for k, v in data.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        _apply_server_defaults_on_update(obj)
        _coerce_uuid_attrs_for_sqlite(obj, db)
        db.flush()
        db.refresh(obj)
        db.commit()
        return _serialize_row(obj)

    @router.delete("/{item_id}", status_code=204)
    def delete_item(item_id: str, db: Session = Depends(get_db)):
        obj = db.get(model, item_id)
        if not obj:
            raise HTTPException(status_code=404, detail=f"{entity.tableName} not found")
        db.delete(obj)
        db.commit()
        return None

    return router
