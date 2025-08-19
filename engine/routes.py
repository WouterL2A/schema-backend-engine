# engine/routes.py
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Set

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import ValidationError
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
    Works for both legacy 'pydantic_models' (v1) and 'pydantic_in' (modular/meta) styles.
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


def _to_payload_dict(obj: Any) -> Dict[str, Any]:
    """Support Pydantic v1 (.dict) and v2 (.model_dump)."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_unset=True)  # pydantic v2
    if hasattr(obj, "dict"):
        return obj.dict(exclude_unset=True)        # pydantic v1
    if isinstance(obj, dict):
        return obj
    return dict(obj)


def _pyd_schema_dict(pyd_model: Any) -> Dict[str, Any]:
    """Return an OpenAPI/JSON Schema dict for a Pydantic model (v1/v2)."""
    if pyd_model is None:
        return {}
    # pydantic v2
    if hasattr(pyd_model, "model_json_schema"):
        return pyd_model.model_json_schema()
    # pydantic v1
    if hasattr(pyd_model, "schema"):
        return pyd_model.schema()
    return {}


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

    for name, model_class in sqlalchemy_models.items():
        Name: str = name
        Model: Any = model_class

        InModel = pyd_in.get(Name)       # Pydantic model for requests (if available)
        OutModel = pyd_out.get(Name) or InModel  # Prefer explicit out; else fall back to in
        response_model = OutModel

        has_in_model = InModel is not None

        # tiny dependency providers to avoid non-serializable defaults in function signature
        def _dep_model(m: Any = Model) -> Any:
            return m

        def _dep_name(n: str = Name) -> str:
            return n

        def _dep_pyd_in(p: Dict[str, Any] = pyd_in) -> Dict[str, Any]:
            return p

        def _dep_columns(m: Any = Model) -> Set[str]:
            return {c.name for c in m.__table__.columns}

        # Prepare OpenAPI requestBody schema for clean Swagger when we have an input model.
        create_openapi_extra = None
        update_openapi_extra = None
        if has_in_model:
            schema_dict = _pyd_schema_dict(InModel)
            if schema_dict:
                create_openapi_extra = {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": schema_dict
                            }
                        }
                    }
                }
                update_openapi_extra = create_openapi_extra

        # ------------------ CREATE -------------------------------------------
        @router.post(
            f"/{Name}/",
            response_model=response_model,
            tags=[Name],
            summary=f"Create {Name[:-1] if Name.endswith('s') else Name}",
            description=f"Create a new `{Name}` record.",
            openapi_extra=create_openapi_extra,
        )
        def create_item(
            # NOTE: Use plain dict for type checking; we inject correct schema via openapi_extra above.
            item: Dict[str, Any] = Body(...),
            db: Session = Depends(get_db),
            model_name: str = Depends(get_model_name(Name)),
            Model_: Any = Depends(_dep_model),
            pyd_in_map: Dict[str, Any] = Depends(_dep_pyd_in),
        ):
            payload = _to_payload_dict(item)

            # If we have a Pydantic model, (re-)validate into it for strong typing.
            if has_in_model:
                try:
                    validated_obj = InModel(**payload)
                    payload = _to_payload_dict(validated_obj)
                except ValidationError as e:
                    raise HTTPException(status_code=400, detail=f"Invalid data: {e.errors()}")
            else:
                # Fallback: legacy map-based validation
                validated = validate_request_body(payload, model_name, pyd_in_map)
                payload = _to_payload_dict(validated)

            try:
                obj = Model_(**payload)
                db.add(obj)
                db.commit()
                db.refresh(obj)
                return obj
            except IntegrityError as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

        # ------------------ READ ALL (pagination + filters + includes + sort) -------
        @router.get(
            f"/{Name}/",
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
            Model_: Any = Depends(_dep_model),
            Name_: str = Depends(_dep_name),
            column_names_: Set[str] = Depends(_dep_columns),
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

        # ------------------ READ ONE (with includes) ------------------------
        @router.get(
            f"/{Name}/{{item_id}}",
            response_model=response_model,
            tags=[Name],
            summary=f"Get {Name[:-1] if Name.endswith('s') else Name} by ID",
            description=f"Retrieve a single `{Name}` record by primary key. Use `?include=rel1,rel2` to eager-load.",
        )
        def read_item(
            item_id: str,
            include: str | None = Query(None, description="Comma-separated relationships to eager load"),
            db: Session = Depends(get_db),
            Model_: Any = Depends(_dep_model),
        ):
            requested = _parse_include_param(include)
            valid = _valid_relationship_keys(Model_)
            options = [joinedload(getattr(Model_, r)) for r in requested if r in valid]

            if options:
                query = db.query(Model_).options(*options)
                obj = query.get(item_id)  # SQLAlchemy <2 style
            else:
                obj = db.get(Model_, item_id)

            if not obj:
                raise HTTPException(status_code=404, detail="Item not found")
            return obj

        # ------------------ UPDATE -------------------------------------------
        @router.put(
            f"/{Name}/{{item_id}}",
            response_model=response_model,
            tags=[Name],
            summary=f"Update {Name[:-1] if Name.endswith('s') else Name}",
            description=f"Update an existing `{Name}` record by primary key.",
            openapi_extra=update_openapi_extra,
        )
        def update_item(
            item_id: str,
            item: Dict[str, Any] = Body(...),
            db: Session = Depends(get_db),
            model_name: str = Depends(get_model_name(Name)),
            Model_: Any = Depends(_dep_model),
            pyd_in_map: Dict[str, Any] = Depends(_dep_pyd_in),
        ):
            db_obj = db.get(Model_, item_id)
            if not db_obj:
                raise HTTPException(status_code=404, detail="Item not found")

            payload = _to_payload_dict(item)

            if has_in_model:
                try:
                    validated_obj = InModel(**payload)
                    payload = _to_payload_dict(validated_obj)
                except ValidationError as e:
                    raise HTTPException(status_code=400, detail=f"Invalid data: {e.errors()}")
            else:
                validated = validate_request_body(payload, model_name, pyd_in_map)
                payload = _to_payload_dict(validated)

            try:
                for k, v in payload.items():
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
            Model_: Any = Depends(_dep_model),
        ):
            db_obj = db.get(Model_, item_id)
            if not db_obj:
                raise HTTPException(status_code=404, detail="Item not found")
            db.delete(db_obj)
            db.commit()
            return {"status": "deleted"}
