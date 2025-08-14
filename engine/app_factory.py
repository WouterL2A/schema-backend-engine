# engine/app_factory.py
from __future__ import annotations

from fastapi import FastAPI
from typing import Dict, Any

from engine.db import engine
from engine.routes import router, setup_routes
from core.ports import SchemaLoader, ModelBuilder, PydanticBuilder

def create_app(schema_loader: SchemaLoader, model_builder: ModelBuilder, pyd_builder: PydanticBuilder) -> FastAPI:
    app = FastAPI(title="Schema Backend Engine (modular)", version="1.0.0")

    # 1) Load schema (v1 or v3 depending on adapter)
    schema = schema_loader.load()

    # 2) Build SQLAlchemy models from schema
    sa_models = model_builder.build(schema)

    # 3) Create DB tables
    model_builder.Base.metadata.create_all(bind=engine)

    # 4) Build Pydantic input/output models
    pyd_in, pyd_out = pyd_builder.build(sa_models, schema)

    # 5) Register CRUD routes
    models_bundle: Dict[str, Any] = {
        "sqlalchemy_models": sa_models,
        # routes supports both styles; we pass v3-style maps:
        "pydantic_in":  pyd_in,
        "pydantic_out": pyd_out,
        "schema": schema,
    }
    setup_routes(router, models_bundle)
    app.include_router(router)

    @app.get("/")
    def health():
        return {"status": "ok", "mode": schema_loader.__class__.__name__}

    return app
