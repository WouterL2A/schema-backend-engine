# pyright: reportInvalidTypeForm=false
# engine/routes_legacy.py

import logging
from typing import Any, Dict, List, Optional, Set, Type

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, create_model
from sqlalchemy import asc, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.db import get_db
from .routes_base import (
    _is_server_managed,
    _strip_server_managed,
    _apply_server_defaults_on_create,
    _apply_server_defaults_on_update,
    _coerce_uuid_attrs_for_sqlite,
    _model_to_dict,
    _pk_info,
    _sa_cols,
    _col_python_type,
    _ensure_from_attributes,
    _clone_model_with_from_attributes,
    _coerce_value,
)

logger = logging.getLogger(__name__)

def _build_out_model_from_sa(Name: str, Model) -> Type[BaseModel]:
    fields: Dict[str, tuple] = {}
    for col in _sa_cols(Model):
        pytype = _col_python_type(col)
        if getattr(col, "nullable", True):
            fields[col.name] = (Optional[pytype], None)
        else:
            fields[col.name] = (pytype, ...)
    Out = create_model(f"{Name.capitalize()}Out", **fields)  # type: ignore
    Out = _clone_model_with_from_attributes(Out.__name__, Out)
    if hasattr(Out, "model_rebuild"):
        Out.model_rebuild()  # type: ignore[attr-defined]
    return Out

def _build_in_model_from_sa(Name: str, Model) -> Type[BaseModel]:
    pk_col, _ = _pk_info(Model)
    fields: Dict[str, tuple] = {}
    for col in _sa_cols(Model):
        if _is_server_managed(col.name, [pk_col.name]):
            continue  # exclude PK and audit fields from input
        pytype = _col_python_type(col)
        if getattr(col, "nullable", True):
            fields[col.name] = (Optional[pytype], None)
        else:
            fields[col.name] = (pytype, ...)
    In = create_model(f"{Name.capitalize()}In", **fields)  # type: ignore
    if hasattr(In, "model_rebuild"):
        In.model_rebuild()  # type: ignore[attr-defined]
    return In

def setup_routes(router: APIRouter, models: Dict[str, Any]):
    """
    Legacy reflection path.
    models = {
      "sqlalchemy_models": { "users": SAUsers, ... },
      # optional:
      "pydantic_in": { "users": UsersIn, ... },
      "pydantic_out": { "users": UsersOut, ... }
    }
    """
    sqlalchemy_models: Dict[str, Any] = models["sqlalchemy_models"]
    pyd_in: Dict[str, Any] = models.get("pydantic_in") or models.get("pydantic_models") or {}
    pyd_out: Dict[str, Any] = models.get("pydantic_out") or pyd_in

    logger.info("Initializing route setup with SQLAlchemy models: %s", list(sqlalchemy_models.keys()))

    for Name, Model in sqlalchemy_models.items():
        pk_col, pk_pytype = _pk_info(Model)

        InModel: Optional[Type[BaseModel]] = pyd_in.get(Name) or _build_in_model_from_sa(Name, Model)
        OutModel: Optional[Type[BaseModel]] = pyd_out.get(Name) or _build_out_model_from_sa(Name, Model)

        # Ensure PK is present in Out model
        def _has_field(model_cls: Type[BaseModel], field_name: str) -> bool:
            fields = getattr(model_cls, "model_fields", None)
            if isinstance(fields, dict):
                return field_name in fields
            v1_fields = getattr(model_cls, "__fields__", {})
            return field_name in v1_fields

        if not _has_field(OutModel, pk_col.name):
            OutModel = create_model(  # type: ignore
                f"{OutModel.__name__}With{pk_col.name.capitalize()}",
                **{pk_col.name: (Optional[pk_pytype], None)},
                __base__=OutModel,
            )
            if hasattr(OutModel, "model_rebuild"):
                OutModel.model_rebuild()  # type: ignore[attr-defined]

        if not _ensure_from_attributes(OutModel):
            OutModel = _clone_model_with_from_attributes(f"{OutModel.__name__}FromAttrs", OutModel)
            if hasattr(OutModel, "model_rebuild"):
                OutModel.model_rebuild()  # type: ignore[attr-defined]

        # closures
        def make_dep_model(m: Any):
            def _dep_model() -> Any:
                return m
            return _dep_model

        def make_dep_columns(m: Any):
            cols = {c.name for c in m.__table__.columns}
            def _dep_columns() -> Set[str]:
                return cols
            return _dep_columns

        ListResponseModel = create_model(
            f"{Name.capitalize()}ListResponse",
            total=(int, ...),
            limit=(int, ...),
            offset=(int, ...),
            items=(List[OutModel], ...),  # type: ignore[valid-type, reportInvalidTypeForm]
        )
        if hasattr(ListResponseModel, "model_rebuild"):
            ListResponseModel.model_rebuild()  # type: ignore[attr-defined]

        @router.post(f"/{Name}/", response_model=OutModel, tags=[Name], summary=f"Create {Name[:-1] if Name.endswith('s') else Name}")
        def create_item(
            payload: InModel = Body(...),  # type: ignore[valid-type, reportInvalidTypeForm]
            db: Session = Depends(get_db),
            Model_: Any = Depends(make_dep_model(Model)),
        ):
            try:
                clean = _strip_server_managed(_model_to_dict(payload), [pk_col.name])
                obj = Model_(**clean)
                _apply_server_defaults_on_create(obj)
                _coerce_uuid_attrs_for_sqlite(obj, db)
                db.add(obj)
                db.commit()
                db.refresh(obj)
                return obj
            except IntegrityError as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

        @router.get(f"/{Name}/", response_model=ListResponseModel, tags=[Name], summary=f"List {Name}")
        def read_all(
            request: Request,
            sort: Optional[str] = Query(None, description="Column to sort by"),
            order: str = Query("asc", pattern="^(asc|desc)$"),
            limit: int = Query(100, ge=1, le=1000),
            offset: int = Query(0, ge=0),
            db: Session = Depends(get_db),
            Model_: Any = Depends(make_dep_model(Model)),
            column_names_: Set[str] = Depends(make_dep_columns(Model)),
        ):
            query = db.query(Model_)
            reserved = {"limit", "offset", "sort", "order"}
            for key, value in request.query_params.items():
                if key in reserved:
                    continue
                if key in column_names_:
                    col = getattr(Model_, key)
                    query = query.filter(col == _coerce_value(col, value))
            if sort and sort in column_names_:
                col = getattr(Model_, sort)
                query = query.order_by(asc(col) if order == "asc" else desc(col))
            total = query.count()
            items = query.offset(offset).limit(limit).all()
            return {"total": total, "limit": limit, "offset": offset, "items": items}

        @router.get(f"/{Name}/{{item_id}}", response_model=OutModel, tags=[Name], summary=f"Get {Name[:-1] if Name.endswith('s') else Name} by ID")
        def read_item(item_id: str, db: Session = Depends(get_db), Model_: Any = Depends(make_dep_model(Model))):
            try:
                typed_id = pk_pytype(item_id)
            except Exception:
                typed_id = item_id
            obj = db.get(Model_, typed_id)
            if not obj:
                raise HTTPException(status_code=404, detail="Item not found")
            return obj

        # One handler for BOTH verbs to avoid 405s
        @router.api_route(f"/{Name}/{{item_id}}", methods=["PATCH", "PUT"], response_model=OutModel, tags=[Name],
                          summary=f"Update {Name[:-1] if Name.endswith('s') else Name}")
        def update_item(
            item_id: str,
            payload: InModel = Body(...),  # type: ignore[valid-type, reportInvalidTypeForm]
            db: Session = Depends(get_db),
            Model_: Any = Depends(make_dep_model(Model)),
        ):
            try:
                typed_id = pk_pytype(item_id)
            except Exception:
                typed_id = item_id

            db_obj = db.get(Model_, typed_id)
            if not db_obj:
                raise HTTPException(status_code=404, detail="Item not found")
            try:
                clean = _strip_server_managed(_model_to_dict(payload), [pk_col.name])
                for k, v in clean.items():
                    setattr(db_obj, k, v)
                _apply_server_defaults_on_update(db_obj)
                _coerce_uuid_attrs_for_sqlite(db_obj, db)
                db.commit()
                db.refresh(db_obj)
                return db_obj
            except IntegrityError as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

        @router.delete(f"/{Name}/{{item_id}}", tags=[Name], summary=f"Delete {Name[:-1] if Name.endswith('s') else Name}")
        def delete_item(item_id: str, db: Session = Depends(get_db), Model_: Any = Depends(make_dep_model(Model))):
            try:
                typed_id = pk_pytype(item_id)
            except Exception:
                typed_id = item_id
            db_obj = db.get(Model_, typed_id)
            if not db_obj:
                raise HTTPException(status_code=404, detail="Item not found")
            db.delete(db_obj)
            db.commit()
            return {"status": "deleted", "id": typed_id}
