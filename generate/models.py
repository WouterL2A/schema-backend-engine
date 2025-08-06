from sqlalchemy import Column, String, DateTime, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, declarative_base
from generate.loader import load_schema
import uuid
from datetime import datetime
from pydantic import create_model

Base = declarative_base()

def map_type(col_type: str):
    type_map = {
        "uuid": UUID(as_uuid=True),
        "string": String,
        "datetime": DateTime,
        "int": Integer,
    }
    if col_type not in type_map:
        raise ValueError(f"Unsupported column type: {col_type}. Supported types: {list(type_map.keys())}")
    return type_map[col_type]

def generate_models():
    schema = load_schema()
    models = {}
    pydantic_models = {}

    # Validate foreign key references
    table_names = {table["name"] for table in schema["tables"]}
    for table in schema["tables"]:
        for col in table["columns"]:
            if "foreign_key" in col:
                fk_table, fk_column = col["foreign_key"].split(".")
                if fk_table not in table_names:
                    raise ValueError(f"Foreign key references unknown table: {fk_table}")

    for table in schema["tables"]:
        class_attrs = {
            "__tablename__": table["name"],
            "__table_args__": {"extend_existing": True}
        }

        for col in table["columns"]:
            col_type = map_type(col["type"])
            kwargs = {}

            if col.get("primary"):
                kwargs["primary_key"] = True
                if col["type"] == "uuid":
                    kwargs["default"] = uuid.uuid4
            if col.get("unique"):
                kwargs["unique"] = True
            if col.get("default") == "now":
                kwargs["default"] = datetime.utcnow
            if col.get("nullable", False):
                kwargs["nullable"] = True
            if "foreign_key" in col:
                fk_table, fk_column = col["foreign_key"].split(".")
                kwargs["ForeignKey"] = f"{fk_table}.{fk_column}"

            if "ForeignKey" in kwargs:
                class_attrs[col["name"]] = Column(
                    col_type, ForeignKey(kwargs["ForeignKey"]), **{k: v for k, v in kwargs.items() if k != "ForeignKey"}
                )
            else:
                class_attrs[col["name"]] = Column(col_type, **kwargs)

        models[table["name"]] = type(table["name"].capitalize(), (Base,), class_attrs)

        # Build Pydantic model, excluding id since it's database-generated
        pydantic_fields = {}
        for col in table["columns"]:
            if col.get("name") != "id":  # Exclude id from Pydantic input model
                pydantic_type = {
                    "uuid": str,
                    "string": str,
                    "datetime": str,
                    "int": int
                }[col["type"]]
                is_nullable = col.get("nullable", False)
                pydantic_fields[col["name"]] = (pydantic_type, None if is_nullable else ...)
        pydantic_models[table["name"]] = create_model(f"{table['name'].capitalize()}In", **pydantic_fields)

    # Add relationships
    for table in schema["tables"]:
        model = models[table["name"]]
        for col in table["columns"]:
            if "foreign_key" in col:
                fk_table = col["foreign_key"].split(".")[0]
                rel_name = col.get("relationship_name", fk_table.rstrip("s"))
                setattr(model, rel_name, relationship(models[fk_table]))

    return models, pydantic_models