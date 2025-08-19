# engine/main.py
import sys
import logging
from typing import Dict, Type
from fastapi import FastAPI
from pydantic import BaseModel, create_model
from sqlalchemy.orm import DeclarativeMeta

from engine.db import engine
from generate.loader import load_schema, InvalidSchemaError
from generate.models import generate_models, Base
from engine.routes import router, setup_routes

log = logging.getLogger("engine.main")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Schema Backend Engine", version="1.0.0")

def build_pydantic_models(sa_models: Dict[str, any]) -> Dict[str, Type[BaseModel]]:
    """Create input Pydantic models for each SQLAlchemy model (exclude PK)."""
    p_models: Dict[str, Type[BaseModel]] = {}
    for name, sa_cls in sa_models.items():
        fields = {}
        columns = sa_cls.columns if not isinstance(sa_cls, DeclarativeMeta) else sa_cls.__table__.columns
        for col in columns:
            if not col.primary_key:
                fields[col.name] = (col.type.python_type, ...)
        p_models[name] = create_model(f"{name.capitalize()}In", **fields)
    return p_models

try:
    # 1) Validate schema & load it
    schema = load_schema()

    # 2) Build SQLAlchemy models in memory
    sa_models = generate_models()             # dict: tableName -> SA class or Table

    # 3) Create DB tables
    Base.metadata.create_all(bind=engine)

    # 4) Build Pydantic input models (for your routes' validation)
    p_models = build_pydantic_models(sa_models)

    # 5) Shape the structure routes.py expects, then register CRUD routes
    models_bundle = {
        "sqlalchemy_models": sa_models,
        "pydantic_models": p_models,
        "schema": schema,
    }
    setup_routes(router, models_bundle)
    app.include_router(router)

except InvalidSchemaError as e:
    log.error(f"Startup failed: {e}")
    sys.exit(1)

@app.get("/")
def health():
    return {"status": "ok"}
