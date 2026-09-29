from collections.abc import Generator

from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import engine


SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session