# pyright: reportInvalidTypeForm=false
# engine/routes.py
import logging
from typing import Any, Callable, Dict, List, Set, Type, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, create_model
from sqlalchemy import asc, desc
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from engine.db import get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


# -------------------------- helpers --------------------------

def _model_to_dict(model_obj: BaseModel) -> dict:
    """Dump a Pydantic model to dict with exclude_unset, v1/v2 safe."""
    if hasattr(model_obj, "model_dump"):     # Pydantic v2
        return model_obj.model_dump(exclude_unset=True)
    return model_obj.dict(exclude_unset=True)  # Pydantic v1


def _parse_include_param(include_param: Optional[str]) -> List[str]:
    if not include_param:
        return []
    return [p.strip() for p in include_param.split(",") if p.strip()]


def _valid_relationship_keys(Model) -> Set[str]:
    """Return the relationship attribute names actually defined on the SQLAlchemy model."""
    return {rel.key for rel in sa_inspect(Model).relationships}


def _pk_info(Model):
    insp = sa_inspect(Model)
    pk_cols = list(insp.primary_key)
    if not pk_cols:
        # Fallback: first column
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
    """
    Return True if model is configured for ORM serialization.
    Pydantic v2: model_config={"from_attributes": True}
    Pydantic v1: class Config: orm_mode = True
    """
    # v2
    cfg = getattr(model_cls, "model_config", None)
    if isinstance(cfg, dict) and cfg.get("from_attributes") is True:
        return True
    # v1
    Cfg = getattr(model_cls, "Config", None)
    if Cfg and getattr(Cfg, "orm_mode", False):
        return True
    return False


def _clone_model_with_from_attributes(name: str, base: Type[BaseModel]) -> Type[BaseModel]:
    """
    Create a subclass that sets from_attributes=True (v2) or orm_mode (v1)
    without changing fields.
    """
    attrs = {"__doc__": f"{name} (from_attributes enabled)"}
    # v2 config
    attrs["model_config"] = {
        **(getattr(base, "model_config", {}) if hasattr(base, "model_config") else {}),
        "from_attributes": True,
    }
    return type(name, (base,), attrs)


def _build_out_model_from_sa(Name: str, Model) -> Type[BaseModel]:
    """
    Build a Pydantic Out model from SQLAlchemy columns with from_attributes=True.
    Ensures PK (e.g., id) is present.
    """
    fields: Dict[str, tuple] = {}
    for col in _sa_cols(Model):
        pytype = _col_python_type(col)
        # Required vs optional: if column nullable -> optional with default None
        if getattr(col, "nullable", True):
            fields[col.name] = (Optional[pytype], None)
        else:
            fields[col.name] = (pytype, ...)
    Out = create_model(f"{Name.capitalize()}Out", **fields)  # type: ignore
    # Enable from_attributes
    Out = _clone_model_with_from_attributes(Out.__name__, Out)
    return Out


def _build_in_model_from_sa(Name: str, Model) -> Type[BaseModel]:
    """
    Build a Pydantic In model from SQLAlchemy columns, **excluding PK** by default.
    Nullable -> optional with None.
    """
    pk_col, _ = _pk_info(Model)
    fields: Dict[str, tuple] = {}
    for col in _sa_cols(Model):
        if col.name == pk_col.name:
            continue  # exclude PK from input
        pytype = _col_python_type(col)
        if getattr(col, "nullable", True):
            fields[col.name] = (Optional[pytype], None)
        else:
            fields[col.name] = (pytype, ...)
    In = create_model(f"{Name.capitalize()}In", **fields)  # type: ignore
    return In


def _coerce_value(col, raw: str) -> Any:
    """Cast a raw query param to the column's python type for filtering."""
    pytype = _col_python_type(col)
    try:
        return pytype(raw)
    except Exception:
        return raw


# -------------------------- route factory --------------------------

def setup_routes(router: APIRouter, models: Dict[str, Any]):
    """
    Accepts either of these shapes:

    v1 (legacy)
      {
        "sqlalchemy_models": { "users": Users, ... },
        "pydantic_models":   { "users": UsersIn, ... },
      }

    modular/meta
      {
        "sqlalchemy_models": { "users": Users, ... },
        "pydantic_in":       { "users": UsersIn, ... },
        "pydantic_out":      { "users": UsersOut, ... },
      }
    """
    sqlalchemy_models: Dict[str, Any] = models["sqlalchemy_models"]

    # Input models map (prefer modular/meta key, fall back to legacy)
    pyd_in: Dict[str, Any] = models.get("pydantic_in") or models.get("pydantic_models") or {}

    # Output/response models map (prefer modular/meta; otherwise fall back to input models)
    pyd_out: Dict[str, Any] = models.get("pydantic_out") or pyd_in

    logger.info("Initializing route setup with SQLAlchemy models: %s", list(sqlalchemy_models.keys()))

    for Name, Model in sqlalchemy_models.items():
        # --- Primary key info (used throughout) ---
        pk_col, pk_pytype = _pk_info(Model)

        # --- Compute or fix In/Out models ---
        InModel: Optional[Type[BaseModel]] = pyd_in.get(Name)
        OutModel: Optional[Type[BaseModel]] = pyd_out.get(Name)

        if OutModel is None:
            logger.warning("No output Pydantic model for %s; building one from SQLAlchemy model.", Name)
            OutModel = _build_out_model_from_sa(Name, Model)

        # Ensure OutModel contains the PK field (e.g., 'id') even if user-supplied model omitted it.
        def _has_field(model_cls: Type[BaseModel], field_name: str) -> bool:
            # v2
            fields = getattr(model_cls, "model_fields", None)
            if isinstance(fields, dict):
                return field_name in fields
            # v1
            v1_fields = getattr(model_cls, "__fields__", {})
            return field_name in v1_fields

        if not _has_field(OutModel, pk_col.name):
            # Augment OutModel with the PK field; keep existing fields via __base__
            OutModel = create_model(  # type: ignore
                f"{OutModel.__name__}With{pk_col.name.capitalize()}",
                **{pk_col.name: (Optional[pk_pytype], None)},
                __base__=OutModel,
            )

        # Ensure from_attributes for ORM serialization
        if not _ensure_from_attributes(OutModel):
            OutModel = _clone_model_with_from_attributes(f"{OutModel.__name__}FromAttrs", OutModel)

        if InModel is None:
            logger.warning("No input Pydantic model for %s; building one from SQLAlchemy model (excluding PK).", Name)
            InModel = _build_in_model_from_sa(Name, Model)

        # --- closure-based deps: no default class values in signatures ---
        def make_dep_model(m: Any):
            def _dep_model() -> Any:
                return m
            return _dep_model

        def make_dep_name(n: str):
            def _dep_name() -> str:
                return n
            return _dep_name

        def make_dep_columns(m: Any):
            cols = {c.name for c in m.__table__.columns}
            def _dep_columns() -> Set[str]:
                return cols
            return _dep_columns

        # --- typed list response model so /{Name}/ serializes items correctly ---
        ListResponseModel = create_model(
            f"{Name.capitalize()}ListResponse",
            total=(int, ...),
            limit=(int, ...),
            offset=(int, ...),
            items=(List[OutModel], ...),  # type: ignore[valid-type]
        )

        @router.post(
            f"/{Name}/",
            response_model=OutModel,
            tags=[Name],
            summary=f"Create {Name[:-1] if Name.endswith('s') else Name}",
            description=(
                f"Create a new `{Name}` record.\n\n"
                "Send a **bare JSON object** for the record (e.g. `{{ \"name\": \"...\" }}`)."
            ),
        )
        def create_item(
            payload: InModel = Body(...),  # type: ignore[valid-type]
            db: Session = Depends(get_db),
            Model_: Any = Depends(make_dep_model(Model)),
        ):
            try:
                obj = Model_(**_model_to_dict(payload))
                db.add(obj)
                db.commit()
                db.refresh(obj)
                return obj
            except IntegrityError as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

        @router.get(
            f"/{Name}/",
            response_model=ListResponseModel,
            tags=[Name],
            summary=f"List {Name}",
            description=(
                f"List `{Name}` with pagination and simple filters. "
                "Use query params to filter by column equality, e.g. `?email=foo@bar.com`. "
                "Pagination via `limit` and `offset`. "
                "Use `?include=rel1,rel2` to eager-load relationships. "
                "Use `?sort=field&order=asc|desc` for sorting."
            ),
        )
        def read_all(
            request: Request,
            include: Optional[str] = Query(None, description="Comma-separated relationships to eager load"),
            sort: Optional[str] = Query(None, description="Column to sort by"),
            order: str = Query("asc", pattern="^(asc|desc)$"),
            limit: int = Query(100, ge=1, le=1000),
            offset: int = Query(0, ge=0),
            db: Session = Depends(get_db),
            Model_: Any = Depends(make_dep_model(Model)),
            Name_: str = Depends(make_dep_name(Name)),
            column_names_: Set[str] = Depends(make_dep_columns(Model)),
        ):
            query = db.query(Model_)

            # Eager loads
            requested = _parse_include_param(include)
            valid = _valid_relationship_keys(Model_)
            for rel in requested:
                if rel in valid:
                    query = query.options(joinedload(getattr(Model_, rel)))
                else:
                    logger.warning(
                        "Skipping unknown relationship '%s' on model '%s'. Valid: %s",
                        rel, Name_, sorted(valid)
                    )

            # Filters (cast query params to column types)
            reserved = {"limit", "offset", "include", "sort", "order"}
            for key, value in request.query_params.items():
                if key in reserved:
                    continue
                if key in column_names_:
                    col = getattr(Model_, key)
                    query = query.filter(col == _coerce_value(col, value))

            # Sorting
            if sort and sort in column_names_:
                col = getattr(Model_, sort)
                query = query.order_by(asc(col) if order == "asc" else desc(col))

            total = query.count()
            items = query.offset(offset).limit(limit).all()
            return {"total": total, "limit": limit, "offset": offset, "items": items}

        @router.get(
            f"/{Name}/{{item_id}}",
            response_model=OutModel,
            tags=[Name],
            summary=f"Get {Name[:-1] if Name.endswith('s') else Name} by ID",
            description=f"Retrieve a single `{Name}` record by primary key. Use `?include=rel1,rel2` to eager-load.",
        )
        def read_item(
            item_id: str,
            include: Optional[str] = Query(None, description="Comma-separated relationships to eager load"),
            db: Session = Depends(get_db),
            Model_: Any = Depends(make_dep_model(Model)),
        ):
            # Cast path param to PK python type
            try:
                typed_id = pk_pytype(item_id)
            except Exception:
                typed_id = item_id

            requested = _parse_include_param(include)
            valid = _valid_relationship_keys(Model_)
            options = [joinedload(getattr(Model_, r)) for r in requested if r in valid]

            obj = db.get(Model_, typed_id, options=tuple(options)) if options else db.get(Model_, typed_id)
            if not obj:
                raise HTTPException(status_code=404, detail="Item not found")
            return obj

        @router.put(
            f"/{Name}/{{item_id}}",
            response_model=OutModel,
            tags=[Name],
            summary=f"Update {Name[:-1] if Name.endswith('s') else Name}",
            description=(f"Update an existing `{Name}` record by primary key.\n\nSend a **bare JSON object**."),
        )
        def update_item(
            item_id: str,
            payload: InModel = Body(...),  # type: ignore[valid-type]
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
                for k, v in _model_to_dict(payload).items():
                    setattr(db_obj, k, v)
                db.commit()
                db.refresh(db_obj)
                return db_obj
            except IntegrityError as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

        @router.delete(
            f"/{Name}/{{item_id}}",
            tags=[Name],
            summary=f"Delete {Name[:-1] if Name.endswith('s') else Name}",
            description=f"Delete a `{Name}` record by primary key.",
        )
        def delete_item(
            item_id: str,
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
            db.delete(db_obj)
            db.commit()
            return {"status": "deleted", "id": typed_id}
