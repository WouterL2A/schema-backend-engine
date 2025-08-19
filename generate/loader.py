# generate/loader.py
import json
from pathlib import Path
from jsonschema import ValidationError
from jsonschema.validators import Draft7Validator

class InvalidSchemaError(Exception):
    pass

def _resolve_spec_path(spec_uri: str | None) -> Path:
    """
    Resolve the JSON-Schema file path from a $schema URI/path with fallbacks.
    """
    candidates = []
    if spec_uri:
        candidates.append(Path(spec_uri))
    # Common local fallbacks
    candidates.append(Path("schema_definitions/modelSchema.json"))
    candidates.append(Path("modelSchema.json"))

    for p in candidates:
        if p.exists():
            return p

    # Last resort: keep previous behavior (but this likely isn't a spec)
    legacy = Path("schema_definitions/schema_v1.json")
    if legacy.exists():
        return legacy

    raise InvalidSchemaError(
        f"Could not locate a JSON-Schema spec. Tried: {', '.join(str(c) for c in candidates)}"
    )

def load_schema(path: str = "schema.json") -> dict:
    meta_path = Path(path)
    if not meta_path.exists():
        raise InvalidSchemaError(f"Schema file not found at {path}")

    data = json.loads(meta_path.read_text(encoding="utf-8"))

    # Resolve the spec from the metadata's $schema (preferred) or fallbacks
    spec_path = _resolve_spec_path(data.get("$schema"))

    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise InvalidSchemaError(f"Failed to read spec at {spec_path}: {e}") from e

    try:
        Draft7Validator.check_schema(spec)
        Draft7Validator(spec).validate(data)
    except ValidationError as e:
        raise InvalidSchemaError(f"Schema validation failed: {e.message}") from e

    return data
