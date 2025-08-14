from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Set

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from engine.db import get_db
from passlib.context import CryptContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
router = APIRouter()


# -------------------------- helpers --------------------------

def validate_request_body(data: dict, model_name: str, pydantic_models: Dict[str, Any]):
    logger.info("Validating data for model: %s", model_name)
    if model_name not in pydantic_models:
        raise HTTPException(status_code=400, detail=f"Model {model_name} not found")
    try:
        return pydantic_models[model_name](**data)
    except ValidationError as e:
        logger.error("Validation failed for %s: %s", model_name, e.errors())
        raise HTTPException(status_code=400, detail=f"Invalid data: {e.errors()}")


def get_model_name(name: str) -> Callable[[], str]:
    def _get_model_name():
        return name
    return _get_model_name


def _parse_include_param(include_param: str | None) -> List[str]:
    if not include_param:
        return []
    if isinstance(include_param, str):
        # support ?include=a,b,c
        return [p.strip() for p in include_param.split(",") if p.strip()]
    return []


def _valid_relationship_keys(Model) -> Set[str]:
    """Return the relationship attribute names actually defined on the SQLAlchemy model."""
    return {rel.key for rel in sa_inspect(Model).relationships}


# -------------------------- route factory --------------------------

def setup_routes(router: APIRouter, models: Dict[str, Any]):
    """
    models = {
        "sqlalchemy_models": { "users": Users, "roles": Roles, ... },
        "pydantic_models":   { "users": UsersModel, "roles": RolesModel, ... },
        # optional: "schema": {...}
    }
    """
    sqlalchemy_models: Dict[str, Any] = models["sqlalchemy_models"]
    pydantic_models: Dict[str, Any] = models["pydantic_models"]
    schema = models.get("schema", {})

    logger.info("Initializing route setup with SQLAlchemy models: %s", list(sqlalchemy_models.keys()))

    for name, model_class in sqlalchemy_models.items():
        # Bind loop vars into defaults to avoid late-binding closure bugs
        Name = name
        Model = model_class

        # simple column list for filtering
        column_names = {c.name for c in Model.__table__.columns}

        # ------------------ CREATE -------------------------------------------
        @router.post(
            f"/{Name}/",
            response_model=pydantic_models[Name],
            tags=[Name],
            summary=f"Create {Name[:-1] if Name.endswith('s') else Name}",
            description=f"Create a new `{Name}` record.",
        )
        def create_item(
            item: Any = Body(...),
            db: Session = Depends(get_db),
            model_name: str = Depends(get_model_name(Name)),
            Model_: Any = Model,  # bind
        ):
            item_dict = item.dict() if hasattr(item, "dict") else item
            validated_item = validate_request_body(item_dict, model_name, pydantic_models)
            try:
                obj = Model_(**validated_item.dict())
                db.add(obj)
                db.commit()
                db.refresh(obj)
                return obj
            except IntegrityError as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

        # ------------------ READ ALL (pagination + filters + includes) -------
        @router.get(
            f"/{Name}/",
            tags=[Name],
            summary=f"List {Name}",
            description=(
                f"List `{Name}` with pagination and simple filters. "
                "Use query params to filter by column equality, e.g. `?email=foo@bar.com`. "
                "Pagination via `limit` and `offset`. "
                "Use `?include=rel1,rel2` to eager-load relationships."
            ),
        )
        def read_all(
            request: Request,
            include: str | None = Query(None, description="Comma-separated relationships to eager load"),
            limit: int = Query(100, ge=1, le=1000),
            offset: int = Query(0, ge=0),
            db: Session = Depends(get_db),
            Model_: Any = Model,                 # bind
            Name_: str = Name,                   # bind
            column_names_: Set[str] = column_names,  # bind
        ):
            query = db.query(Model_)

            # Eager-load only requested, valid relationships
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

            # simple equality filters from query params (only known columns)
            reserved = {"limit", "offset", "include"}
            for key, value in request.query_params.items():
                if key in reserved:
                    continue
                if key in column_names_:
                    query = query.filter(getattr(Model_, key) == value)

            total = query.count()
            items = query.offset(offset).limit(limit).all()
            return {"total": total, "limit": limit, "offset": offset, "items": items}

        # ------------------ READ ONE (with includes) ------------------------
        @router.get(
            f"/{Name}/{{item_id}}",
            tags=[Name],
            summary=f"Get {Name[:-1] if Name.endswith('s') else Name} by ID",
            description=f"Retrieve a single `{Name}` record by primary key. Use `?include=rel1,rel2` to eager-load.",
        )
        def read_item(
            item_id: str,
            include: str | None = Query(None, description="Comma-separated relationships to eager load"),
            db: Session = Depends(get_db),
            Model_: Any = Model,    # bind
            Name_: str = Name,      # bind
        ):
            requested = _parse_include_param(include)
            valid = _valid_relationship_keys(Model_)
            options = [joinedload(getattr(Model_, r)) for r in requested if r in valid]

            if options:
                # build a query with options then use scalar_one_or_none pattern
                query = db.query(Model_).options(*options)
                obj = query.get(item_id)  # if on SQLAlchemy <2; otherwise prefer db.get
            else:
                # SQLAlchemy 2.0 style
                obj = db.get(Model_, item_id)

            if not obj:
                raise HTTPException(status_code=404, detail="Item not found")
            return obj

        # ------------------ UPDATE -------------------------------------------
        @router.put(
            f"/{Name}/{{item_id}}",
            response_model=pydantic_models[Name],
            tags=[Name],
            summary=f"Update {Name[:-1] if Name.endswith('s') else Name}",
            description=f"Update an existing `{Name}` record by primary key.",
        )
        def update_item(
            item_id: str,
            item: Any = Body(...),
            db: Session = Depends(get_db),
            model_name: str = Depends(get_model_name(Name)),
            Model_: Any = Model,  # bind
        ):
            item_dict = item.dict() if hasattr(item, "dict") else item
            validated_item = validate_request_body(item_dict, model_name, pydantic_models)
            db_obj = db.get(Model_, item_id)
            if not db_obj:
                raise HTTPException(status_code=404, detail="Item not found")
            try:
                for k, v in validated_item.dict(exclude_unset=True).items():
                    setattr(db_obj, k, v)
                db.commit()
                db.refresh(db_obj)
                return db_obj
            except IntegrityError as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

        # ------------------ DELETE -------------------------------------------
        @router.delete(
            f"/{Name}/{{item_id}}",
            tags=[Name],
            summary=f"Delete {Name[:-1] if Name.endswith('s') else Name}",
            description=f"Delete a `{Name}` record by primary key.",
        )
        def delete_item(
            item_id: str,
            db: Session = Depends(get_db),
            Model_: Any = Model,  # bind
        ):
            db_obj = db.get(Model_, item_id)
            if not db_obj:
                raise HTTPException(status_code=404, detail="Item not found")
            db.delete(db_obj)
            db.commit()
            return {"status": "deleted"}
