python schema_converter.py draft_entities.json -o schema.meta.json \
  --schema-uri schema_definitions/modelSchema.json


from schema_converter import convert_draft7_entities_to_meta
import json

with open("draft_entities.json", "r", encoding="utf-8") as f:
    draft = json.load(f)

meta = convert_draft7_entities_to_meta(draft, schema_uri_for_output="schema_definitions/modelSchema.json")

with open("schema.meta.json", "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=2)


Notes / assumptions

Arrays & objects are skipped unless FK hints (x-refTable/x-refColumn) are present.

$ref resolution supports internal pointers like #/definitions/....

Type mapping is conservative (e.g., number → FLOAT). If you want DECIMAL(p,s), say the word and I’ll add support for x-precision/x-scale.
