from __future__ import annotations
from typing import Optional
from sqlalchemy import (
    String, Integer, BigInteger, Float, Boolean, Date, DateTime, Text,
    LargeBinary, JSON as SA_JSON, Numeric
)
from sqlalchemy.dialects import postgresql
# MySQL specifics not required unless you want dialect-specific types beyond String

def sqlalchemy_type(
    data_type: str,
    *,
    length: Optional[int] = None,
    precision: Optional[int] = None,
    scale: Optional[int] = None,
    dialect: str = "generic",
):
    dt = (data_type or "").upper()

    if dt == "UUID":
        if dialect == "postgresql":
            return postgresql.UUID(as_uuid=True)
        # sqlite/mysql default: store as 36-char string
        return String(36)

    if dt == "VARCHAR":
        return String(int(length or 255))

    if dt == "TEXT":
        return Text()

    if dt == "INTEGER":
        return Integer()

    if dt == "BIGINT":
        return BigInteger()

    if dt == "DECIMAL":
        return Numeric(precision=int(precision or 18), scale=int(scale or 6))

    if dt == "FLOAT":
        return Float()

    if dt == "BOOLEAN":
        return Boolean()

    if dt == "DATE":
        return Date()

    if dt == "TIMESTAMP":
        return DateTime(timezone=True)

    if dt == "JSON":
        return SA_JSON()

    if dt == "BLOB":
        return LargeBinary()

    # Fallback
    return String(255)
