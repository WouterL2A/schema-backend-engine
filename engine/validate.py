import json
from jsonschema import validate, ValidationError

# Load schema and model
with open("ddl_schema.json") as f:
    schema = json.load(f)
with open("model.json") as f:
    model = json.load(f)

# Validate
try:
    validate(instance=model, schema=schema)
    print("JSON model is valid!")
except ValidationError as e:
    print("Validation error:", e.message)