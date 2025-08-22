# engine/main.py
from __future__ import annotations
import os
import logging
import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, inspect

from engine.db import engine, get_settings
from engine.meta_models import ModelMeta
from engine.ddl_builder import create_all_from_meta
from engine.routes import build_crud_router
from engine.migrate_additive import plan_and_apply_additive  # safety-gated below

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

# ---- Load meta (path overridable) ----
# Recommended: set MODEL_META_PATH to your compiled IR, e.g.:
#   set MODEL_META_PATH=C:\GitHub\Schema_Meta_System\meta\modelSchema.json
META_PATH = os.getenv("MODEL_META_PATH", "schema/schema.meta.json")
try:
    meta_path = Path(META_PATH).resolve()
    raw = meta_path.read_text(encoding="utf-8")
    meta = ModelMeta.model_validate_json(raw)
    logger.info("Loaded meta from %s with %d tables", str(meta_path), len(meta.tables))
except Exception as e:
    logger.error("Failed to load/validate meta at %s: %s", META_PATH, e)
    raise

# Compute a content hash of the meta (used for apply ACK)
try:
    meta_sha256 = hashlib.sha256(meta_path.read_bytes()).hexdigest()
    meta_ack_hint = meta_sha256[:8]
except Exception:
    meta_sha256 = "-"
    meta_ack_hint = "-"

# Capture DB state *before* any create_all() to detect pre-existing schemas
insp_before = inspect(engine)
had_existing_tables = bool(insp_before.get_table_names())

# Optional: drop & recreate for dev only (dangerous)
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

# ---- Build models + create tables (idempotent; creates only) ----
try:
    models = create_all_from_meta(engine, meta, dialect=settings.DIALECT)
    logger.info("SQLAlchemy models created for: %s", ", ".join(models.keys()))
except Exception as e:
    logger.error("DDL/model creation failed: %s", e)
    raise

# ---- SAFETY-GATED ADDITIVE MIGRATION -----------------------------------------
# Plan only:
#   set ENGINE_APPLY_ADDITIVE_PLAN=1
# Apply (requires *all* of):
#   set ENGINE_APPLY_ADDITIVE=1
#   set ENGINE_APPLY_ADDITIVE_ACK=<first 8 of meta SHA256 shown by plan>   (e.g. %s)
#   set ENGINE_ALLOW_REMOTE=1          (only if DB is not localhost/sqlite)
#   set ENGINE_ALLOW_NONEMPTY=1        (only if DB had tables before start)
#
# These interlocks prevent accidental use by new users.
plan_flag  = os.getenv("ENGINE_APPLY_ADDITIVE_PLAN") == "1"
apply_flag = os.getenv("ENGINE_APPLY_ADDITIVE") == "1"
ack_value  = os.getenv("ENGINE_APPLY_ADDITIVE_ACK", "")
allow_remote   = os.getenv("ENGINE_ALLOW_REMOTE") == "1"
allow_nonempty = os.getenv("ENGINE_ALLOW_NONEMPTY") == "1" or not had_existing_tables

# Determine if DB is "remote" (anything not sqlite or localhost)
url = engine.url
is_sqlite = url.get_backend_name().startswith("sqlite")
is_remote_host = (not is_sqlite) and (url.host not in (None, "localhost", "127.0.0.1"))

if plan_flag:
    logger.warning(
        "ENGINE_APPLY_ADDITIVE_PLAN=1 → PLAN mode only. "
        "Meta hash (SHA256) %s (ack hint: %s). Remote DB: %s. Had existing tables: %s",
        meta_sha256, meta_ack_hint, is_remote_host, had_existing_tables
    )
    plan_and_apply_additive(engine, meta, dialect=settings.DIALECT, apply=False)

if apply_flag:
    # Two-man rule: require hash ACK to match this meta
    if meta_ack_hint == "-" or ack_value != meta_ack_hint:
        logger.error(
            "Refusing to APPLY additive changes. You must set ENGINE_APPLY_ADDITIVE_ACK=%s "
            "to match the current meta (from PLAN output).", meta_ack_hint
        )
        raise SystemExit(2)

    # Block remote DBs unless explicitly allowed
    if is_remote_host and not allow_remote:
        logger.error(
            "Refusing to APPLY on remote DB (%s). Set ENGINE_ALLOW_REMOTE=1 to confirm you understand the risk.",
            str(url)
        )
        raise SystemExit(2)

    # Block schemas that existed before boot unless explicitly allowed
    if had_existing_tables and not allow_nonempty:
        logger.error(
            "Refusing to APPLY on a non-empty schema (tables existed before start). "
            "Set ENGINE_ALLOW_NONEMPTY=1 to confirm you understand the risk."
        )
        raise SystemExit(2)

    logger.warning(
        "APPLYING additive changes (env-acknowledged). Meta ack=%s, remote=%s, preexisting=%s",
        ack_value, is_remote_host, had_existing_tables
    )
    plan_and_apply_additive(engine, meta, dialect=settings.DIALECT, apply=True)

# -----------------------------------------------------------------------------

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
