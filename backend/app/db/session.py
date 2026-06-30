import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from typing import Generator

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://breakout_user:breakout_secure_pass123@localhost:5432/breakout_db"
)

# Support fallback to SQLite for local lightweight testing
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db() -> Generator:
    """Dependency for DB session injection in FastAPI routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
