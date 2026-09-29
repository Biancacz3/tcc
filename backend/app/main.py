import logging

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.devices import router as devices_router
from app.db.engine import engine


logger = logging.getLogger(__name__)

app = FastAPI(
    title="Monitoramento de Pragas em Acelga",
    version="0.1.0",
)

app.include_router(
    devices_router,
    prefix="/api/v1",
)


@app.get("/api/v1/health", tags=["Sistema"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/db", tags=["Sistema"])
def health_db() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError:
        logger.exception("Falha na comunicacao com o PostgreSQL.")

        raise HTTPException(
            status_code=503,
            detail="Banco de dados indisponivel",
        ) from None

    return {
        "status": "ok",
        "database": "connected",
    }
