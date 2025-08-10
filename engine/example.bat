# Validate schema
python generate.py validate

# Rebuild tables from schema.json (destructive)
python generate.py reset

# Export DDL
python generate.py export-ddl --dialect=sqlite --out=schema.sqlite.sql
python generate.py export-ddl --dialect=postgres --out=schema.pg.sql
python generate.py export-ddl --dialect=mssql --out=schema.mssql.sql
