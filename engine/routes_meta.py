# pyright: reportInvalidTypeForm=false
# engine/routes_meta.py
#
# Meta-driven CRUD router:
# - POST/PATCH/PUT request bodies EXCLUDE server-managed fields (incl. PK)
# - GET responses tolerate nullable DB columns (Optional[...] + exclude_none)
# - PATCH and PUT are SEPARATE routes with distinct names (no duplicate operationIds)

import logging
from typing import Any, Dict, List, Optional, Tuple, Type
from decimal import Decimal
from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, create_model
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from engine.db import get_db
from .routes_base import (
    SERVER_MANAGED_FIELDS,
    _is_server_managed,
    _strip_server_managed,
    _apply_server_defaults_on_create,
    _apply_server_defaults_on_update,
    _coerce_uuid_attrs_for_sqlite,
    _string_columns,
    _apply_sort,
    _serialize_row,
)

try:
    # meta model types
    from engine.meta_models import ModelMeta, Table, Column as MetaCol  # type: ignore
except Exception:  # pragma: no cover - import-time flexibility for tooling
    ModelMeta = Any  # type: ignore
    Table = Any      # type: ignore
    MetaCol = Any    # type: ignore

logger = logging.getLogger(__name__)


# --------------------------- type mapping helpers -----------------------------

def _sqltype_to_pytype(dt: str) -> Any:
    dt = (dt or "").upper()
    if dt in ("VARCHAR", "CHAR", "UUID", "TEXT"):
        return str
    if dt in ("INTEGER", "INT", "BIGINT", "SMALLINT"):
        return int
    if dt in ("NUMERIC", "DECIMAL"):
        return Decimal
    if dt in ("FLOAT", "REAL", "DOUBLE"):
        return float
    if dt == "BOOLEAN":
        return bool
    if dt == "DATE":
        return date
    if dt in ("TIMESTAMP", "DATETIME"):
        return datetime
    if dt == "JSON":
        return Any
    if dt in ("BLOB", "BYTEA"):
        return bytes
    return Any


def _is_required_for_create(col: MetaCol, pk: List[str]) -> bool:
    """
    A column is required on CREATE if:
      - it's NOT server-managed (incl. PK), and
      - it's NOT nullable, and
      - it has NO default value.
    """
    name = col.columnName
    if _is_server_managed(name, pk):
        return False
    if getattr(col, "isNullable", False):
        return False
    if getattr(col, "defaultValue", None) is not None:
        return False
    return True


# -------------------- build Pydantic models from the meta ---------------------

def _make_pydantic_models_from_meta(
    entity: Table,
) -> Tuple[Type[BaseModel], Type[BaseModel], Type[BaseModel], Type[BaseModel]]:
    """
    Returns (CreateModel, ReadModel, UpdateModel, ListResponseModel)

    - CreateModel: excludes server-managed fields (incl. PK). Required iff non-nullable & no default.
    - UpdateModel: excludes server-managed fields; all optional (partial update).
    - ReadModel:   includes ALL fields; nullable columns are Optional[...] with default None.
    """
    pk = list(entity.primaryKey or [])

    create_fields: Dict[str, Tuple[Any, Any]] = {}
    read_fields: Dict[str, Tuple[Any, Any]] = {}
    update_fields: Dict[str, Tuple[Any, Any]] = {}

    for col in entity.columns:
        name = col.columnName
        pytype = _sqltype_to_pytype(getattr(col, "dataType", ""))
        is_nullable = bool(getattr(col, "isNullable", False))
        has_default = getattr(col, "defaultValue", None) is not None

        # READ model: include everything; make nullable fields Optional
        read_ann = Optional[pytype] if is_nullable else pytype
        read_default = None if is_nullable else ...
        read_fields[name] = (read_ann, read_default)

        # CREATE / UPDATE: exclude server-managed (incl. PK, id/created_at/etc.)
        if _is_server_managed(name, pk):
            continue

        # CREATE requiredness
        if _is_required_for_create(col, pk):
            create_fields[name] = (pytype, ...)
        else:
            create_fields[name] = (Optional[pytype], None)

        # UPDATE is always optional (partial)
        update_fields[name] = (Optional[pytype], None)

    base = entity.tableName.title().replace("_", "")
    CreateModel = create_model(f"{base}Create", __base__=BaseModel, **create_fields)
    ReadModel   = create_model(f"{base}Read",   __base__=BaseModel, **read_fields)
    UpdateModel = create_model(f"{base}Update", __base__=BaseModel, **update_fields)
    ListModel   = create_model(
        f"{base}ListResponse",
        __base__=BaseModel,
        total=(int, ...),
        limit=(int, ...),
        offset=(int, ...),
        items=(List[ReadModel], ...),
    )

    # pydantic v2: ensure models are fully built
    for m in (CreateModel, ReadModel, UpdateModel, ListModel):
        if hasattr(m, "model_rebuild"):
            m.model_rebuild()  # type: ignore[attr-defined]

    return CreateModel, ReadModel, UpdateModel, ListModel


# ------------------------ CRUD router per meta entity -------------------------

def build_crud_router(entity: Table, model, meta: ModelMeta) -> APIRouter:
    """
    Build an APIRouter for a single entity driven by the meta.
    - Prefix: /{tableName}
    - Tags:   [{tableName}]
    """
    router = APIRouter(prefix=f"/{entity.tableName}", tags=[entity.tableName])
    pk = list(entity.primaryKey or [])
    if len(pk) != 1:
        # Only single-column PKs are supported here (matches your engine.main guard)
        return router

    CreateModel, ReadModel, UpdateModel, ListResponseModel = _make_pydantic_models_from_meta(entity)

    # -------- LIST
    @router.get(
        "/",
        response_model=ListResponseModel,
        response_model_exclude_none=True,  # tolerate NULLs coming from DB
    )
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

    # -------- GET
    @router.get(
        "/{item_id}",
        response_model=ReadModel,
        response_model_exclude_none=True,  # tolerate NULLs coming from DB
    )
    def get_item(item_id: str, db: Session = Depends(get_db)):
        obj = db.get(model, item_id)
        if not obj:
            raise HTTPException(status_code=404, detail=f"{entity.tableName} not found")
        return _serialize_row(obj)

    # -------- CREATE (POST): EXCLUDES server-managed fields from request model
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

    # -------- UPDATE (PATCH): separate route to avoid duplicate operationIds
    @router.patch(
        "/{item_id}",
        response_model=ReadModel,
        name=f"{entity.tableName}__partial_update_item",
    )
    def partial_update_item(
        item_id: str,
        payload: UpdateModel = Body(...),
        db: Session = Depends(get_db),
    ):
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

    # -------- REPLACE (PUT): separate route with its own name/operationId
    @router.put(
        "/{item_id}",
        response_model=ReadModel,
        name=f"{entity.tableName}__replace_item",
    )
    def replace_item(
        item_id: str,
        payload: UpdateModel = Body(...),
        db: Session = Depends(get_db),
    ):
        obj = db.get(model, item_id)
        if not obj:
            raise HTTPException(status_code=404, detail=f"{entity.tableName} not found")
        data = _strip_server_managed(payload.model_dump(exclude_unset=True), pk)
        # Replace semantics here mirror patch (no field clearing); adjust if desired.
        for k, v in data.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        _apply_server_defaults_on_update(obj)
        _coerce_uuid_attrs_for_sqlite(obj, db)
        db.flush()
        db.refresh(obj)
        db.commit()
        return _serialize_row(obj)

    # -------- DELETE
    @router.delete("/{item_id}", status_code=204)
    def delete_item(item_id: str, db: Session = Depends(get_db)):
        obj = db.get(model, item_id)
        if not obj:
            raise HTTPException(status_code=404, detail=f"{entity.tableName} not found")
        db.delete(obj)
        db.commit()
        return None

    return router
