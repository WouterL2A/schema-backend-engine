# pyright: reportInvalidTypeForm=false
# engine/routes.py
# NOTE: Do NOT enable "from __future__ import annotations" here. We want real types, not strings.

import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Type
from uuid import UUID, uuid4
from decimal import Decimal
from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, create_model, Field
from sqlalchemy import asc, desc, select, func, String, Text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from engine.db import get_db

# Optional meta imports for build_crud_router (used by engine.main)
try:
    from engine.meta_models import ModelMeta, Table, Column as MetaCol  # type: ignore
except Exception:
    ModelMeta = Any  # type: ignore
    Table = Any      # type: ignore
    MetaCol = Any    # type: ignore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# -----------------------------------------------------------------------------
# Policy: server-managed fields (never accepted in request payloads)
# -----------------------------------------------------------------------------
SERVER_MANAGED_FIELDS: Set[str] = {
    # snake_case
    "id", "created_at", "updated_at", "created_by", "updated_by",
    # camelCase
    "createdAt", "updatedAt", "createdBy", "updatedBy",
}

def _now_utc() -> datetime:
    # Use naive UTC for portability; DB timezone rules can be applied at the engine level
    return datetime.utcnow()

def _is_server_managed(name: str, pk_names: Optional[List[str]] = None) -> bool:
    if pk_names and name in pk_names:
        return True
    return name in SERVER_MANAGED_FIELDS

def _strip_server_managed(data: Dict[str, Any], pk_names: Optional[List[str]] = None) -> Dict[str, Any]:
    sm = set(SERVER_MANAGED_FIELDS) | set(pk_names or [])
    return {k: v for k, v in data.items() if k not in sm}

def _apply_server_defaults_on_create(obj: Any) -> None:
    # id
    if hasattr(obj, "id") and getattr(obj, "id", None) in (None, "", 0):
        try:
            setattr(obj, "id", uuid4())
        except Exception:
            pass
    # timestamps
    ts = _now_utc()
    for name in ("created_at", "updated_at", "createdAt", "updatedAt"):
        if hasattr(obj, name) and getattr(obj, name, None) in (None, ""):
            try:
                setattr(obj, name, ts)
            except Exception:
                pass
    # actor
    for name in ("created_by", "updated_by", "createdBy", "updatedBy"):
        if hasattr(obj, name) and getattr(obj, name, None) in (None, ""):
            try:
                setattr(obj, name, "system")
            except Exception:
                pass

def _apply_server_defaults_on_update(obj: Any) -> None:
    ts = _now_utc()
    for name in ("updated_at", "updatedAt"):
        if hasattr(obj, name):
            try:
                setattr(obj, name, ts)
            except Exception:
                pass
    for name in ("updated_by", "updatedBy"):
        if hasattr(obj, name):
            try:
                setattr(obj, name, "system")
            except Exception:
                pass

# -----------------------------------------------------------------------------
# SQLite UUID hotfix: coerce UUIDs to strings right before flush/commit (SQLite only)
# -----------------------------------------------------------------------------
def _is_sqlite(db) -> bool:
    try:
        bind = getattr(db, "get_bind", lambda: None)() or db.bind
        return bool(bind and bind.dialect.name == "sqlite")
    except Exception:
        return False

def _coerce_uuid_attrs_for_sqlite(obj, db) -> None:
    """For SQLite only: convert any uuid.UUID values on ORM columns to str."""
    if not _is_sqlite(db):
        return
    for col in obj.__table__.columns:
        try:
            val = getattr(obj, col.name, None)
        except Exception:
            continue
        if isinstance(val, UUID):
            setattr(obj, col.name, str(val))

# -----------------------------------------------------------------------------
# Shared helpers
# -----------------------------------------------------------------------------
def _model_to_dict(model_obj: BaseModel) -> dict:
    if hasattr(model_obj, "model_dump"):     # Pydantic v2
        return model_obj.model_dump(exclude_unset=True)
    return model_obj.dict(exclude_unset=True)  # Pydantic v1

def _pk_info(Model):
    insp = sa_inspect(Model)
    pk_cols = list(insp.primary_key)
    if not pk_cols:
        first_col = list(Model.__table__.columns)[0]
        return first_col, getattr(first_col.type, "python_type", str)
    pk = pk_cols[0]
    pytype = getattr(pk.type, "python_type", str)
    return pk, pytype

def _sa_cols(Model):
    return list(Model.__table__.columns)

def _col_python_type(col) -> Any:
    try:
        return col.type.python_type
    except Exception:
        return Any

def _ensure_from_attributes(model_cls: Type[BaseModel]) -> bool:
    cfg = getattr(model_cls, "model_config", None)
    if isinstance(cfg, dict) and cfg.get("from_attributes") is True:
        return True
    Cfg = getattr(model_cls, "Config", None)
    if Cfg and getattr(Cfg, "orm_mode", False):
        return True
    return False

def _clone_model_with_from_attributes(name: str, base: Type[BaseModel]) -> Type[BaseModel]:
    attrs = {"__doc__": f"{name} (from_attributes enabled)"}
    attrs["model_config"] = {
        **(getattr(base, "model_config", {}) if hasattr(base, "model_config") else {}),
        "from_attributes": True,
    }
    return type(name, (base,), attrs)

def _coerce_value(col, raw: str) -> Any:
    pytype = _col_python_type(col)
    try:
        return pytype(raw)
    except Exception:
        return raw

def _string_columns(model) -> List:
    cols = []
    for c in model.__table__.columns:
        try:
            if isinstance(c.type, (String, Text)):
                cols.append(c)
        except Exception:
            pass
    return cols

def _apply_sort(model, sort: Optional[str]):
    order_by = []
    if not sort:
        return order_by
    fields = [s.strip() for s in sort.split(",") if s.strip()]
    for f in fields:
        desc_ = f.startswith("-")
        name = f[1:] if desc_ else f
        if hasattr(model, name):
            col = getattr(model, name)
            order_by.append(col.desc() if desc_ else col.asc())
    return order_by

def _serialize_row(obj) -> Dict[str, Any]:
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}

# -----------------------------------------------------------------------------
# Path A — Legacy setup: reflect SQLAlchemy models (setup_routes)
# -----------------------------------------------------------------------------
def _build_out_model_from_sa(Name: str, Model) -> Type[BaseModel]:
    fields: Dict[str, tuple] = {}
    for col in _sa_cols(Model):
        pytype = _col_python_type(col)
        # Required vs optional: if column nullable -> optional with default None
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

        # closures for dependencies (avoid class objects in signature default)
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
                _coerce_uuid_attrs_for_sqlite(obj, db)  # <-- SQLite UUID hotfix
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

            # Filters (cast query params to column types)
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

        @router.put(f"/{Name}/{{item_id}}", response_model=OutModel, tags=[Name], summary=f"Update {Name[:-1] if Name.endswith('s') else Name}")
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
                _coerce_uuid_attrs_for_sqlite(db_obj, db)  # <-- SQLite UUID hotfix
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

# -----------------------------------------------------------------------------
# Path B — Meta-driven router per entity (what engine.main imports)
# -----------------------------------------------------------------------------
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
    if col.columnName in (pk or []):
        return False  # server-managed PK: not required in Create input
    if getattr(col, "isNullable", None):
        return False
    if col.defaultValue is not None:
        return False
    if _is_server_managed(col.columnName, pk):
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

        # Read: include everything
        read_fields[name] = (py_t, Field(default=None))

        # Create: exclude server-managed
        if not _is_server_managed(name, pk):
            if _is_required_meta(c, pk):
                create_fields[name] = (py_t, ...)
            else:
                create_fields[name] = (Optional[py_t], None)

        # Update: exclude server-managed (clients cannot set id/audit)
        if not _is_server_managed(name, pk):
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
        _coerce_uuid_attrs_for_sqlite(obj, db)  # <-- SQLite UUID hotfix
        db.add(obj)
        db.flush()
        db.refresh(obj)
        db.commit()
        return _serialize_row(obj)

    @router.patch("/{item_id}", response_model=ReadModel)
    def update_item(item_id: str, payload: UpdateModel = Body(...), db: Session = Depends(get_db)):  # type: ignore[reportInvalidTypeForm]
        obj = db.get(model, item_id)
        if not obj:
            raise HTTPException(status_code=404, detail=f"{entity.tableName} not found")
        data = _strip_server_managed(payload.model_dump(exclude_unset=True), pk)
        for k, v in data.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        _apply_server_defaults_on_update(obj)
        _coerce_uuid_attrs_for_sqlite(obj, db)  # <-- SQLite UUID hotfix
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
