from generate.loader import load_schema

def test_schema_is_valid():
    load_schema()  # raises on failure
