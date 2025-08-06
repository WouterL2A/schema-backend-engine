import json
from jsonschema import validate, ValidationError

def load_schema(path="schema.json"):
    schema_validator = {
        "type": "object",
        "required": ["tables"],
        "properties": {
            "tables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "columns"],
                    "properties": {
                        "name": {"type": "string"},
                        "columns": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["name", "type"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "type": {"enum": ["uuid", "string", "datetime", "int"]},
                                    "primary": {"type": "boolean"},
                                    "unique": {"type": "boolean"},
                                    "nullable": {"type": "boolean"},
                                    "default": {"type": "string"},
                                    "foreign_key": {"type": "string", "pattern": r"^[a-zA-Z_]+\.[a-zA-Z_]+$"},
                                    "relationship_name": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    try:
        with open(path, "r") as f:
            schema = json.load(f)
        validate(instance=schema, schema=schema_validator)
        return schema
    except FileNotFoundError:
        raise FileNotFoundError(f"Schema file not found at: {path}")
    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON in schema file: {path}")
    except ValidationError as e:
        raise ValueError(f"Schema validation failed: {e.message}")
    