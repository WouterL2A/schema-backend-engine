#$in = "schema\schema_v3.json"
$in = "schema_definitions\schema_v3.json"

#$out = "schema\schema.meta.json"
$out = "schema.json"

$schemaDeff = "schema_definitions\modelSchema.json"

#python .\conversion\schema_converter.py $in -o $out --schema-uri schema_definitions/modelSchema.json
python .\conversion\schema_converter.py $in -o $out --schema-uri $schemaDeff 