# adapters/v1/pyd_v1.py
from __future__ import annotations
from typing import Dict, Any, Tuple, Type
from pydantic import BaseModel, create_model
from sqlalchemy.orm import DeclarativeMeta

class V1PydanticBuilder:
    """
    Builds simple Pydantic models from SQLAlchemy columns (your current approach).
    - pyd_in: input models (exclude PKs, required by default)
    - pyd_out: reuse pyd_in for responses (keeps behavior identical to v1)
    """
    def build(self, sa_models: Dict[str, DeclarativeMeta], schema: dict) -> Tuple[Dict[str, Type[BaseModel]], Dict[str, Type[BaseModel]]]:
        p_in: Dict[str, Type[BaseModel]] = {}
        for name, sa_cls in sa_models.items():
            fields = {}
            for col in sa_cls.__table__.columns:
                if not col.primary_key:
                    try:
                        py_t = col.type.python_type
                    except Exception:
                        py_t = str
                    fields[col.name] = (py_t, ...)
            p_in[name] = create_model(f"{name.capitalize()}In", **fields)

        # For v1, use same models for output
        p_out = p_in.copy()
        return p_in, p_out
