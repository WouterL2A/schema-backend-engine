# adapters/v1/models_v1.py
from __future__ import annotations
from typing import Dict, Any

from generate.models import generate_models, Base  # your existing v1 builder

class V1ModelBuilder:
    def build(self, schema: dict) -> Dict[str, Any]:
        # generate_models() already reads from load_schema internally; schema arg not used here,
        # but we keep the signature to satisfy the port.
        return generate_models()

    @property
    def Base(self):
        return Base
