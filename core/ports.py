# core/ports.py
from __future__ import annotations
from typing import Protocol, Dict, Any, Tuple

class SchemaLoader(Protocol):
    def load(self) -> dict: ...

class ModelBuilder(Protocol):
    """Builds SQLAlchemy models from a schema."""
    def build(self, schema: dict) -> Dict[str, Any]: ...
    @property
    def Base(self): ...  # Declarative base

class PydanticBuilder(Protocol):
    """
    Builds Pydantic models for API I/O.
    Returns (pyd_in, pyd_out) dicts keyed by entity/table name.
    """
    def build(self, sa_models: Dict[str, Any], schema: dict) -> Tuple[Dict[str, Any], Dict[str, Any]]: ...
