# engine/main.py
from __future__ import annotations
import os
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from engine.db import engine, get_settings
from engine.meta_models import ModelMeta
from engine.ddl_builder import create_all_from_meta
from engine.routes import build_crud_router

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL, logging.INFO))
logger = logging.getLogger("engine.main")

app = FastAPI(title="Schema Backend Engine", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _sanitize_meta(meta: ModelMeta) -> ModelMeta:
    """
    Fix common authoring artifacts:
      - FK referencedTable like 'process_definition/properties/id' -> 'process_definition'
      - FK referencedColumn missing or pointer-like -> 'id'
      - Ensure form_definition.form_schema exists as JSON
    Returns a new validated ModelMeta.
    """
    d = meta.model_dump(mode="json")
    fk_fixes = 0
    fk_defaulted = 0
    form_schema_added = False

    for t in d.get("tables", []):
        # Ensure form_definition.form_schema
        if t.get("tableName") == "form_definition":
            cols = t.get("columns", [])
            if not any(c.get("columnName") == "form_schema" for c in cols):
                cols.append({
                    "columnName": "form_schema",
                    "dataType": "JSON",
                    "isNullable": False
                })
                t["columns"] = cols
                form_schema_added = True

        # Normalize FK targets
        for fk in (t.get("foreignKeys") or []):
            rt = fk.get("referencedTable")
            if isinstance(rt, str):
                new_rt = rt.lstrip("#/").split("/")[0]
                if new_rt != rt:
                    fk["referencedTable"] = new_rt
                    fk_fixes += 1

            rc = fk.get("referencedColumn")
            if isinstance(rc, str) and rc:
                new_rc = rc.split("/")[-1].split(".")[-1]
                if new_rc != rc:
                    fk["referencedColumn"] = new_rc
                    fk_fixes += 1

            if not fk.get("referencedColumn"):
                fk["referencedColumn"] = "id"
                fk_defaulted += 1

    meta_fixed = ModelMeta.model_validate(d)
    logger.info(
        "Sanitized meta: fk_fixes=%s fk_defaultedColumn=%s form_schema_added=%s",
        fk_fixes, fk_defaulted, form_schema_added
    )
    return meta_fixed

# ---- Load meta ----
META_PATH = os.getenv("MODEL_META_PATH", "schema/schema.meta.json")
try:
    meta_path = Path(META_PATH).resolve()
    raw = meta_path.read_text(encoding="utf-8")
    meta = ModelMeta.model_validate_json(raw)
    logger.info("Loaded meta from %s with %d tables", str(meta_path), len(meta.tables))
except Exception as e:
    logger.error("Failed to load/validate meta at %s: %s", META_PATH, e)
    raise

# Optional: drop & recreate for dev
if os.getenv("ENGINE_RECREATE") == "1":
    from engine.ddl_builder import Base  # Base used inside builder
    logger.warning("ENGINE_RECREATE=1 → dropping all tables before create_all()")
    try:
        Base.metadata.drop_all(bind=engine)
        logger.info("Dropped all tables")
    except Exception as e:
        logger.exception("Drop-all failed: %s", e)

# Sanitize meta in-memory (handles pointer-like FK targets)
meta = _sanitize_meta(meta)

# ---- Build models + create tables ----
try:
    models = create_all_from_meta(engine, meta, dialect=settings.DIALECT)
    logger.info("SQLAlchemy models created for: %s", ", ".join(models.keys()))
except Exception as e:
    logger.error("DDL/model creation failed: %s", e)
    raise

# ---- Register CRUD routers (single-PK only) ----
for t in meta.tables:
    m = models.get(t.tableName)
    if not m:
        continue
    if t.primaryKey and len(t.primaryKey) == 1:
        app.include_router(build_crud_router(t, m, meta))
    else:
        logger.warning("Skipping router for %s (PK not single-column)", t.tableName)

@app.get("/meta")
def get_meta():
    return meta.model_dump(mode="json")

@app.get("/entities")
def list_entities():
    return [t.tableName for t in meta.tables]

@app.get("/healthz")
def healthz():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/readyz")
def readyz():
    return {"status": "ready"}
