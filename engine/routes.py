# pyright: reportInvalidTypeForm=false
# engine/routes.py
import logging
from typing import Any, Callable, Dict, List, Set, Type
from pydantic import BaseModel, create_model, ValidationError
#from typing import Any, Callable, Dict, List, Set

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
#from pydantic import BaseModel, ValidationError
from sqlalchemy import asc, desc
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from engine.db import get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


# -------------------------- helpers --------------------------

def validate_request_body(data: dict, model_name: str, pyd_models_in: Dict[str, Any]):
    """
    Validates incoming payloads using the provided Pydantic input models map.
    Kept for compatibility, but create/update now rely on FastAPI parsing.
    """
    if not pyd_models_in or model_name not in pyd_models_in:
        raise HTTPException(status_code=400, detail=f"Model {model_name} not found for validation")
    try:
        return pyd_models_in[model_name](**data)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"Invalid data: {e.errors()}")


def get_model_name(name: str) -> Callable[[], str]:
    def _get_model_name():
        return name
    return _get_model_name


def _parse_include_param(include_param: str | None) -> List[str]:
    if not include_param:
        return []
    return [p.strip() for p in include_param.split(",") if p.strip()]


def _valid_relationship_keys(Model) -> Set[str]:
    """Return the relationship attribute names actually defined on the SQLAlchemy model."""
    return {rel.key for rel in sa_inspect(Model).relationships}


def _model_to_dict(model_obj: BaseModel) -> dict:
    """Dump a Pydantic model to dict with exclude_unset, v1/v2 safe."""
    if hasattr(model_obj, "model_dump"):     # Pydantic v2
        return model_obj.model_dump(exclude_unset=True)
    return model_obj.dict(exclude_unset=True)  # Pydantic v1


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

    # inside setup_routes(...) loop:
    for name, model_class in sqlalchemy_models.items():
        Name: str = name
        Model: Any = model_class

        InModel = pyd_in.get(Name)
        OutModel = pyd_out.get(Name) or InModel  # fallback is fine if InModel has from_attributes=True

        if InModel is None:
            logger.warning("No input Pydantic model for %s; request body will not be typed in OpenAPI.", Name)

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
        if OutModel is not None:
            ListResponseModel = create_model(
                f"{Name.capitalize()}ListResponse",
                total=(int, ...),
                limit=(int, ...),
                offset=(int, ...),
                items=(List[OutModel], ...),  # <- items will be coerced to OutModel
            )
        else:
            ListResponseModel = create_model(
                f"{Name.capitalize()}ListResponse",
                total=(int, ...),
                limit=(int, ...),
                offset=(int, ...),
                items=(List[dict], ...),  # last-resort fallback
            )

        @router.post(
            f"/{Name}/",
            response_model=OutModel,
            tags=[Name],
            summary=f"Create {Name[:-1] if Name.endswith('s') else Name}",
            description=(
                f"Create a new `{Name}` record.\n\n"
                "Send a **bare JSON object** for the record (e.g. `{ \"name\": \"...\" }`)."
            ),
        )
        def create_item(
            payload: InModel = Body(...),
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
            response_model=ListResponseModel,  # <- add a response model
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
            include: str | None = Query(None, description="Comma-separated relationships to eager load"),
            sort: str | None = Query(None, description="Column to sort by"),
            order: str = Query("asc", pattern="^(asc|desc)$"),
            limit: int = Query(100, ge=1, le=1000),
            offset: int = Query(0, ge=0),
            db: Session = Depends(get_db),
            Model_: Any = Depends(make_dep_model(Model)),
            Name_: str = Depends(make_dep_name(Name)),
            column_names_: Set[str] = Depends(make_dep_columns(Model)),
        ):
            query = db.query(Model_)

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

            reserved = {"limit", "offset", "include", "sort", "order"}
            for key, value in request.query_params.items():
                if key in reserved:
                    continue
                if key in column_names_:
                    query = query.filter(getattr(Model_, key) == value)

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
            include: str | None = Query(None, description="Comma-separated relationships to eager load"),
            db: Session = Depends(get_db),
            Model_: Any = Depends(make_dep_model(Model)),
        ):
            requested = _parse_include_param(include)
            valid = _valid_relationship_keys(Model_)
            options = [joinedload(getattr(Model_, r)) for r in requested if r in valid]

            # SQLAlchemy 2.0-style fetch with loader options:
            obj = db.get(Model_, item_id, options=tuple(options)) if options else db.get(Model_, item_id)
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
            payload: InModel = Body(...),
            db: Session = Depends(get_db),
            Model_: Any = Depends(make_dep_model(Model)),
        ):
            db_obj = db.get(Model_, item_id)
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
            db_obj = db.get(Model_, item_id)
            if not db_obj:
                raise HTTPException(status_code=404, detail="Item not found")
            db.delete(db_obj)
            db.commit()
            return {"status": "deleted"}
        