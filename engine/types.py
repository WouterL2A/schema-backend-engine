# engine/types.py
import uuid
from sqlalchemy.types import TypeDecorator, CHAR
try:
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
except Exception:
    PG_UUID = None  # keep import-safe if dialect not installed

class GUID(TypeDecorator):
    """Platform-independent GUID/UUID.

    - PostgreSQL: uses UUID type (as_uuid=True)
    - Others (SQLite, MySQL, etc.): stores as CHAR(36) string
    """
    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if PG_UUID is not None and dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if PG_UUID is not None and dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        # non-PG: store as 36-char string
        return str(value) if isinstance(value, uuid.UUID) else str(uuid.UUID(str(value)))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
