# engine/main_v1.py
from __future__ import annotations

from engine.app_factory import create_app
from adapters.v1.loader_v1 import V1SchemaLoader
from adapters.v1.models_v1 import V1ModelBuilder
from adapters.v1.pyd_v1 import V1PydanticBuilder

app = create_app(
    schema_loader=V1SchemaLoader(),
    model_builder=V1ModelBuilder(),
    pyd_builder=V1PydanticBuilder(),
)
