# generate/loader.py
import json
from pathlib import Path
from jsonschema import ValidationError
from jsonschema.validators import Draft7Validator  # ⬅ add this

class InvalidSchemaError(Exception):
    pass

def load_schema(path: str = "schema.json") -> dict:
    schema_path = Path(path)
    if not schema_path.exists():
        raise InvalidSchemaError(f"Schema file not found at {path}")

    data = json.loads(schema_path.read_text(encoding="utf-8"))

    spec_path = Path("schema_definitions/schema_v1.json")
    if not spec_path.exists():
        raise InvalidSchemaError(
            "Schema spec not found at schema_definitions/schema_v1.json"
        )
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    try:
        # Use Draft-07 explicitly to avoid deprecation warning
        Draft7Validator.check_schema(spec)
        Draft7Validator(spec).validate(data)
    except ValidationError as e:
        raise InvalidSchemaError(f"Schema validation failed: {e.message}") from e

    return data
