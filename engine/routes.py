from fastapi import APIRouter, HTTPException, Depends, Body, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from typing import Dict, Any, Callable
from engine.db import get_db
from passlib.context import CryptContext
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
router = APIRouter()

def validate_request_body(data: dict, model_name: str, pydantic_models: Dict):
    logger.info(f"Starting validation for model: {model_name}, available models: {list(pydantic_models.keys())}")
    if model_name not in pydantic_models:
        raise HTTPException(status_code=400, detail=f"Model {model_name} not found")
    try:
        return pydantic_models[model_name](**data)
    except ValidationError as e:
        logger.error(f"Validation failed for {model_name}: {e.errors()}")
        raise HTTPException(status_code=400, detail=f"Invalid data: {e.errors()}")

def get_model_name(name: str) -> Callable[[], str]:
    def _get_model_name():
        logger.info(f"Resolving model name, using bound value: {name}")
        return name
    return _get_model_name

def setup_routes(router: APIRouter, models: Dict[str, Any]):
    sqlalchemy_models = models["sqlalchemy_models"]
    pydantic_models = models["pydantic_models"]
    logger.info(f"Initializing route setup with models: {list(sqlalchemy_models.keys())}, pydantic_models: {list(pydantic_models.keys())}")

    for name, model_class in sqlalchemy_models.items():
        logger.info(f"Setting up endpoint for path: /{name}, model: {model_class.__name__}")

        # Use schema-defined relationships
        relationships = []
        for table in models.get("schema", {"tables": []})["tables"]:
            if table["tableName"] == name:
                relationships = [fk.get("relationshipName", f"{fk['referencedTable']}_{fk['columnName']}")
                                for fk in table.get("foreignKeys", [])]

        @router.post(f"/{name}/", response_model=pydantic_models[name], description=f"Create a new {name.capitalize()} record")
        def create_item(item: Any = Body(...), db: Session = Depends(get_db), model_name: str = Depends(get_model_name(name))):
            logger.info(f"Received request for endpoint: /{name}, model_name: {model_name}, data: {item}")
            item_dict = item.dict() if hasattr(item, "dict") else item
            logger.info(f"Preparing to validate with model: {model_name}, data: {item_dict}")
            validated_item = validate_request_body(item_dict, model_name, pydantic_models)
            logger.info(f"Validation successful for {model_name}, validated data: {validated_item.dict()}")
            db_model = sqlalchemy_models[model_name]
            obj = db_model(**validated_item.dict())
            try:
                db.add(obj)
                db.commit()
                db.refresh(obj)
                logger.info(f"Item created for {model_name} with id: {obj.id}")
                return obj
            except IntegrityError as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

        @router.get(f"/{name}/", description=f"Retrieve all {name.capitalize()} records with related data")
        def read_all(request: Request, db: Session = Depends(get_db), model_name: str = Depends(get_model_name(name))):
            logger.info(f"Received GET request for URL: {request.url.path}, model: {model_class.__name__}, resolved model_name: {model_name}")
            logger.info(f"Starting to retrieve all records for /{name}, model: {sqlalchemy_models[model_name].__name__}")
            logger.info(f"Session state before query: {db}")
            query = db.query(sqlalchemy_models[model_name])
            logger.info(f"Query constructed: {query}")
            for rel in relationships:
                query = query.options(joinedload(getattr(sqlalchemy_models[model_name], rel)))
                logger.info(f"Added joinedload for relationship: {rel}")
            results = query.all()
            logger.info(f"Retrieved records: {results}")
            return results

        @router.get(f"/{name}/{{item_id}}", description=f"Retrieve a single {name.capitalize()} record by ID")
        def read_item(item_id: str, db: Session = Depends(get_db)):
            logger.info(f"Retrieving item {item_id} for /{name}")
            query = db.query(model_class)
            for rel in relationships:
                query = query.options(joinedload(getattr(model_class, rel)))
            obj = query.get(item_id)
            if not obj:
                raise HTTPException(status_code=404, detail="Item not found")
            return obj

        @router.put(f"/{name}/{{item_id}}", response_model=pydantic_models[name], description=f"Update an existing {name.capitalize()} record by ID")
        def update_item(item_id: str, item: Any = Body(...), db: Session = Depends(get_db), model_name: str = Depends(get_model_name(name))):
            logger.info(f"Received update request for /{name}, item_id: {item_id}, model_name: {model_name}, data: {item}")
            item_dict = item.dict() if hasattr(item, "dict") else item
            logger.info(f"Preparing to validate update for {model_name}, data: {item_dict}")
            validated_item = validate_request_body(item_dict, model_name, pydantic_models)
            logger.info(f"Validation successful for {model_name}, validated data: {validated_item.dict()}")
            db_model = sqlalchemy_models[model_name]
            db_obj = db.query(db_model).get(item_id)
            if not db_obj:
                raise HTTPException(status_code=404, detail="Item not found")
            try:
                for k, v in validated_item.dict(exclude_unset=True).items():
                    setattr(db_obj, k, v)
                db.commit()
                db.refresh(db_obj)
                logger.info(f"Item updated for {model_name} with id: {item_id}")
                return db_obj
            except IntegrityError as e:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

        @router.delete(f"/{name}/{{item_id}}", description=f"Delete a {name.capitalize()} record by ID")
        def delete_item(item_id: str, db: Session = Depends(get_db)):
            logger.info(f"Received delete request for /{name}, item_id: {item_id}")
            db_obj = db.query(model_class).get(item_id)
            if not db_obj:
                raise HTTPException(status_code=404, detail="Item not found")
            db.delete(db_obj)
            db.commit()
            return {"status": "deleted"}