# adapters/v1/loader_v1.py
from __future__ import annotations
from generate.loader import load_schema  # your existing v1 loader

class V1SchemaLoader:
    """Loads v1 DSL schema.json validated against schema_definitions/schema_v1.json."""
    def load(self) -> dict:
        return load_schema("schema.json")
