# generate.py
import io
import sys
import typer
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import sqlite, postgresql, mssql

from generate.loader import load_schema, InvalidSchemaError
from generate.models import generate_models, Base
from engine.db import engine

app = typer.Typer(help="Schema-Driven Backend Generator CLI")

# ---------------------------
# Core utilities
# ---------------------------
def _require_valid_schema() -> None:
    try:
        load_schema()  # raises InvalidSchemaError if invalid
    except InvalidSchemaError as e:
        typer.echo(f"❌ {e}")
        raise typer.Exit(code=1)

# ---------------------------
# Commands
# ---------------------------
@app.command(help="Validate schema.json against the project JSON-Schema.")
def validate():
    _require_valid_schema()
    typer.echo("✅ schema.json is valid.")

@app.command(help="Drop and recreate all tables from schema.json (DESTRUCTIVE).")
def reset():
    _require_valid_schema()
    generate_models()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    typer.echo("⚠️  Database reset complete.")

@app.command(help="Export CREATE TABLE DDL for the current schema.")
def export_ddl(
    dialect: str = typer.Option("sqlite", help="Target dialect: sqlite | postgres | mssql"),
    out: str = typer.Option("schema.sql", help="Output .sql file path")
):
    _require_valid_schema()
    # Build SQLAlchemy Table objects in Base.metadata
    generate_models()

    # Pick a SQL dialect (no DB driver needed for compilation)
    dialect_map = {
        "sqlite": sqlite.dialect(),
        "postgres": postgresql.dialect(),
        "mssql": mssql.dialect(),
    }
    di = dialect_map.get(dialect.lower())
    if not di:
        typer.echo("❌ Unknown dialect. Use one of: sqlite | postgres | mssql")
        raise typer.Exit(code=2)

    # Compile CREATE TABLE statements
    buf = io.StringIO()
    for table in Base.metadata.sorted_tables:
        buf.write(str(CreateTable(table).compile(dialect=di)))
        buf.write(";\n\n")

    with open(out, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())

    typer.echo(f"✅ DDL written to {out} (dialect={dialect})")

if __name__ == "__main__":
    app()
