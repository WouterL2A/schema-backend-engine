from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, declarative_base
from engine.db import engine
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import Base from where it's defined (assuming generate/models.py)
try:
    from generate.models import Base  # Adjust if Base is imported differently
except ImportError as e:
    logger.error(f"Failed to import Base from generate.models: {str(e)}")
    raise

def initialize_database():
    logger.info(f"Initializing database with engine: {engine}")
    try:
        with engine.connect() as connection:
            logger.info("Checking existing tables...")
            inspector = inspect(engine)
            existing_tables = inspector.get_table_names()
            logger.info(f"Existing tables: {existing_tables}")
        Base.metadata.create_all(bind=engine)
        with engine.connect() as connection:
            logger.info("Verifying tables after creation...")
            inspector = inspect(engine)
            created_tables = inspector.get_table_names()
            logger.info(f"Tables after creation: {created_tables}")
    except Exception as e:
        logger.error(f"Failed to initialize database: {str(e)}")
        raise

def get_db_session():
    db = Session(bind=engine)
    try:
        yield db
    finally:
        db.close()