from __future__ import annotations
from enum import Enum
from typing import List, Optional, Any
from pydantic import BaseModel, Field

class DataType(str, Enum):
    UUID = "UUID"
    VARCHAR = "VARCHAR"
    TEXT = "TEXT"
    INTEGER = "INTEGER"
    BIGINT = "BIGINT"
    DECIMAL = "DECIMAL"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    JSON = "JSON"
    BLOB = "BLOB"

class Column(BaseModel):
    columnName: str
    dataType: DataType
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    isNullable: Optional[bool] = None
    isUnique: Optional[bool] = None
    defaultValue: Optional[Any] = None

class ForeignKey(BaseModel):
    columnName: str
    referencedTable: str
    referencedColumn: str
    relationshipName: Optional[str] = None

class Index(BaseModel):
    name: str
    columns: List[str]
    unique: Optional[bool] = None

class Table(BaseModel):
    tableName: str
    columns: List[Column]
    primaryKey: Optional[List[str]] = None
    foreignKeys: Optional[List[ForeignKey]] = None
    indexes: Optional[List[Index]] = None

class ModelMeta(BaseModel):
    tables: List[Table]
