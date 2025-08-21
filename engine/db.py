from __future__ import annotations
import os
from functools import lru_cache
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

class Settings:
    DATABASE_URL: str
    DIALECT: str
    LOG_LEVEL: str

    def __init__(self) -> None:
        self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
        self.DIALECT = os.getenv("DIALECT", "sqlite").lower()
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

@lru_cache
def get_settings() -> Settings:
    return Settings()

_settings = get_settings()

# echo can be toggled via LOG_LEVEL if you like
engine = create_engine(_settings.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
