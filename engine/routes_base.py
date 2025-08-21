# pyright: reportInvalidTypeForm=false
# engine/routes_base.py

import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Type
from uuid import UUID, uuid4
from datetime import datetime

from pydantic import BaseModel

from sqlalchemy import String, Text
from sqlalchemy import inspect as sa_inspect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Policy: server-managed fields (never accepted in request payloads)
# -----------------------------------------------------------------------------
SERVER_MANAGED_FIELDS: Set[str] = {
    # snake_case
    "id", "created_at", "updated_at", "created_by", "updated_by",
    # camelCase
    "createdAt", "updatedAt", "createdBy", "updatedBy",
}

def _now_utc() -> datetime:
    return datetime.utcnow()

def _is_server_managed(name: str, pk_names: Optional[List[str]] = None) -> bool:
    if pk_names and name in pk_names:
        return True
    return name in SERVER_MANAGED_FIELDS

def _strip_server_managed(data: Dict[str, Any], pk_names: Optional[List[str]] = None) -> Dict[str, Any]:
    sm = set(SERVER_MANAGED_FIELDS) | set(pk_names or [])
    return {k: v for k, v in data.items() if k not in sm}

def _apply_server_defaults_on_create(obj: Any) -> None:
    # id
    if hasattr(obj, "id") and getattr(obj, "id", None) in (None, "", 0):
        try:
            setattr(obj, "id", uuid4())
        except Exception:
            pass
    # timestamps
    ts = _now_utc()
    for name in ("created_at", "updated_at", "createdAt", "updatedAt"):
        if hasattr(obj, name) and getattr(obj, name, None) in (None, ""):
            try:
                setattr(obj, name, ts)
            except Exception:
                pass
    # actor
    for name in ("created_by", "updated_by", "createdBy", "updatedBy"):
        if hasattr(obj, name) and getattr(obj, name, None) in (None, ""):
            try:
                setattr(obj, name, "system")
            except Exception:
                pass

def _apply_server_defaults_on_update(obj: Any) -> None:
    ts = _now_utc()
    for name in ("updated_at", "updatedAt"):
        if hasattr(obj, name):
            try:
                setattr(obj, name, ts)
            except Exception:
                pass
    for name in ("updated_by", "updatedBy"):
        if hasattr(obj, name):
            try:
                setattr(obj, name, "system")
            except Exception:
                pass

# -----------------------------------------------------------------------------
# SQLite UUID hotfix: coerce UUIDs to strings right before flush/commit (SQLite only)
# -----------------------------------------------------------------------------
def _is_sqlite(db) -> bool:
    try:
        bind = getattr(db, "get_bind", lambda: None)() or db.bind
        return bool(bind and bind.dialect.name == "sqlite")
    except Exception:
        return False

def _coerce_uuid_attrs_for_sqlite(obj, db) -> None:
    """For SQLite only: convert any uuid.UUID values on ORM columns to str."""
    if not _is_sqlite(db):
        return
    for col in obj.__table__.columns:
        try:
            val = getattr(obj, col.name, None)
        except Exception:
            continue
        if isinstance(val, UUID):
            setattr(obj, col.name, str(val))

# -----------------------------------------------------------------------------
# Pydantic/SQLAlchemy helpers
# -----------------------------------------------------------------------------
def _model_to_dict(model_obj: BaseModel) -> dict:
    if hasattr(model_obj, "model_dump"):     # Pydantic v2
        return model_obj.model_dump(exclude_unset=True)
    return model_obj.dict(exclude_unset=True)  # Pydantic v1

def _pk_info(Model):
    insp = sa_inspect(Model)
    pk_cols = list(insp.primary_key)
    if not pk_cols:
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
    cfg = getattr(model_cls, "model_config", None)
    if isinstance(cfg, dict) and cfg.get("from_attributes") is True:
        return True
    Cfg = getattr(model_cls, "Config", None)
    if Cfg and getattr(Cfg, "orm_mode", False):
        return True
    return False

def _clone_model_with_from_attributes(name: str, base: Type[BaseModel]) -> Type[BaseModel]:
    attrs = {"__doc__": f"{name} (from_attributes enabled)"}
    attrs["model_config"] = {
        **(getattr(base, "model_config", {}) if hasattr(base, "model_config") else {}),
        "from_attributes": True,
    }
    return type(name, (base,), attrs)

def _coerce_value(col, raw: str) -> Any:
    pytype = _col_python_type(col)
    try:
        return pytype(raw)
    except Exception:
        return raw

def _string_columns(model) -> List:
    cols = []
    for c in model.__table__.columns:
        try:
            if isinstance(c.type, (String, Text)):
                cols.append(c)
        except Exception:
            pass
    return cols

def _apply_sort(model, sort: Optional[str]):
    order_by = []
    if not sort:
        return order_by
    fields = [s.strip() for s in sort.split(",") if s.strip()]
    for f in fields:
        desc_ = f.startswith("-")
        name = f[1:] if desc_ else f
        if hasattr(model, name):
            col = getattr(model, name)
            order_by.append(col.desc() if desc_ else col.asc())
    return order_by

def _serialize_row(obj) -> Dict[str, Any]:
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}
