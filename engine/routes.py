from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, create_model
from sqlalchemy.orm import Session, joinedload
from typing import Dict, Type, Any
from generate.models import generate_models
from engine.db import get_db

models = generate_models()
router = APIRouter()
pydantic_models: Dict[str, Type[BaseModel]] = {}

def build_pydantic_model(model_name: str, model_class):
    fields = {}
    for col in model_class.__table__.columns:
        if not col.primary_key:
            fields[col.name] = (col.type.python_type, ...)
    return create_model(f"{model_name.capitalize()}In", **fields)

for name, model_class in models.items():
    db_model = model_class
    pyd_model = build_pydantic_model(name, model_class)
    pydantic_models[name] = pyd_model

    @router.post(f"/{name}/")
    def create_item(item: Any, db: Session = Depends(get_db), model=db_model):
        obj = model(**item.dict())
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    @router.get(f"/{name}/")
    def read_all(db: Session = Depends(get_db), model=db_model):
        return db.query(model).options(joinedload("*")).all()

    @router.get(f"/{name}/{{item_id}}")
    def read_item(item_id: str, db: Session = Depends(get_db), model=db_model):
        obj = db.query(model).options(joinedload("*")).get(item_id)
        if not obj:
            raise HTTPException(status_code=404, detail="Item not found")
        return obj

    @router.put(f"/{name}/{{item_id}}")
    def update_item(item_id: str, item: Any, db: Session = Depends(get_db), model=db_model):
        db_obj = db.query(model).get(item_id)
        if not db_obj:
            raise HTTPException(status_code=404, detail="Item not found")
        for k, v in item.dict().items():
            setattr(db_obj, k, v)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    @router.delete(f"/{name}/{{item_id}}")
    def delete_item(item_id: str, db: Session = Depends(get_db), model=db_model):
        db_obj = db.query(model).get(item_id)
        if not db_obj:
            raise HTTPException(status_code=404, detail="Item not found")
        db.delete(db_obj)
        db.commit()
        return {"status": "deleted"}
