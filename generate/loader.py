import json

def load_schema(path="schema.json"):
    with open(path, "r") as f:
        return json.load(f)
