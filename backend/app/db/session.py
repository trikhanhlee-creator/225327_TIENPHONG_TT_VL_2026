from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from typing import Generator

from app.core.config import settings

# Tạo engine
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # Kiểm tra kết nối trước khi dùng
)

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base class cho models
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    Dependency injection cho database session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
