import json
from generate.loader import load_schema, InvalidSchemaError
import sys

def generate_ddl(model):
    ddl = []
    for table in model["tables"]:
        columns = []
        for col in table["columns"]:
            col_def = f"{col['columnName']} {col['dataType']}"
            if col.get("length"):
                col_def += f"({col['length']})"
            if table.get("primaryKey") and col["columnName"] in table["primaryKey"]:
                col_def += " PRIMARY KEY"
            if col.get("isUnique", False):
                col_def += " UNIQUE"
            if not col.get("isNullable", True):
                col_def += " NOT NULL"
            if "defaultValue" in col:
                col_def += f" DEFAULT {col['defaultValue']}"
            for fk in table.get("foreignKeys", []):
                if fk["columnName"] == col["columnName"]:
                    col_def += f" REFERENCES {fk['referencedTable']}({fk['referencedColumn']})"
            columns.append(col_def)
        table_def = f"CREATE TABLE {table['tableName']} (\n  {', '.join(columns)}"
        if table.get("primaryKey") and not any(col["columnName"] in table["primaryKey"] for col in table["columns"]):
            table_def += f",\n  PRIMARY KEY ({', '.join(table['primaryKey'])})"
        table_def += "\n);"
        ddl.append(table_def)
    return "\n".join(ddl)

def main():
    try:
        model = load_schema("schema.json")
        print(generate_ddl(model))
    except InvalidSchemaError as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()