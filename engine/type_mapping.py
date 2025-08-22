# engine/type_mapping.py
from __future__ import annotations
import json
from sqlalchemy import types
from sqlalchemy.dialects import postgresql, mysql

class SQLiteSafeJSON(types.TypeDecorator):
    """
    SQLite-safe JSON that tolerates '', 'null', None and non-JSON strings.
    Stores as TEXT; deserializes on read but never raises.
    """
    impl = types.TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        if isinstance(value, (int, float, bool)):
            return json.dumps(value)
        if isinstance(value, str):
            v = value.strip()
            if not v:
                return None
            # if it's already JSON-looking, keep as-is; otherwise store raw
            try:
                json.loads(v)
                return v
            except Exception:
                return v
        # fallback
        try:
            return json.dumps(value)
        except Exception:
            return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            try:
                value = value.decode("utf-8", errors="ignore")
            except Exception:
                value = str(value)
        if isinstance(value, str):
            v = value.strip()
            if v in ("", "null", "NULL"):
                return None
            try:
                return json.loads(v)
            except Exception:
                # return raw string instead of failing
                return v
        return value

def sqlalchemy_type(
    data_type: str,
    *,
    length: int | None = None,
    precision: int | None = None,
    scale: int | None = None,
    dialect: str = "generic",
):
    """
    Map our meta DataType -> SQLAlchemy Column type.
    `dialect` should start with 'sqlite', 'postgres', 'mysql', or 'generic'.
    """
    dt = (data_type or "").upper()
    d = (dialect or "generic").lower()

    if dt == "UUID":
        # For sqlite and generic, store as 36-char string
        if d.startswith("postgres"):
            try:
                return postgresql.UUID(as_uuid=True)
            except Exception:
                return types.String(36)
        return types.String(36)

    if dt == "VARCHAR":
        return types.String(length or 255)

    if dt == "TEXT":
        return types.Text()

    if dt == "INTEGER":
        return types.Integer()

    if dt == "BIGINT":
        return types.BigInteger()

    if dt == "DECIMAL":
        # sensible defaults
        return types.Numeric(precision or 18, scale or 6)

    if dt == "FLOAT":
        return types.Float()

    if dt == "BOOLEAN":
        return types.Boolean()

    if dt == "DATE":
        return types.Date()

    if dt == "TIMESTAMP":
        # naive DateTime; engines that support server defaults can still set func.now()
        return types.DateTime()

    if dt == "JSON":
        if d.startswith("postgres"):
            # Prefer JSONB
            try:
                return postgresql.JSONB(none_as_null=True)
            except Exception:
                return types.JSON(none_as_null=True)
        if d.startswith("mysql"):
            try:
                return mysql.JSON()
            except Exception:
                return types.JSON()
        # sqlite / generic
        return SQLiteSafeJSON()

    if dt == "BLOB":
        return types.LargeBinary()

    # Fallback (be permissive)
    return types.Text()
