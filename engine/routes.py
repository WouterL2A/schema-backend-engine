# pyright: reportInvalidTypeForm=false
# engine/routes.py
"""
Thin re-export shim to keep imports stable while using a modular routes layout.

- build_crud_router (meta-driven per-entity routes)
- setup_routes (legacy reflection-based routes)
"""

from .routes_meta import build_crud_router  # what engine.main imports
from .routes_legacy import setup_routes     # legacy path for reflected models

__all__ = ["build_crud_router", "setup_routes"]
