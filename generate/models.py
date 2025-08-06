from sqlalchemy import Column, String, DateTime, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, declarative_base
from generate.loader import load_schema
import uuid
from datetime import datetime

Base = declarative_base()

def map_type(col_type: str):
    if col_type == "uuid":
        return UUID(as_uuid=True)
    elif col_type == "string":
        return String
    elif col_type == "datetime":
        return DateTime
    elif col_type == "int":
        return Integer
    else:
        raise ValueError(f"Unsupported type: {col_type}")

def generate_models():
    schema = load_schema()
    models = {}

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

    # Add relationships
    for table in schema["tables"]:
        model = models[table["name"]]
        for col in table["columns"]:
            if "foreign_key" in col:
                fk_table = col["foreign_key"].split(".")[0]
                rel_name = fk_table.rstrip("s")
                setattr(model, rel_name, relationship(models[fk_table]))

    return models
