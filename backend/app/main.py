import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.captures import router as captures_router
from app.api.devices import router as devices_router
from app.db.engine import engine


logger = logging.getLogger(__name__)

app = FastAPI(
    title="Monitoramento de Pragas em Acelga",
    version="0.1.0",
)

app.include_router(devices_router, prefix="/api/v1")
app.include_router(captures_router, prefix="/api/v1")

app.mount(
    "/painel",
    StaticFiles(directory=Path(__file__).resolve().parent / "web", html=True),
    name="painel",
)


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/painel/")


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
        raise HTTPException(status_code=503, detail="Banco de dados indisponivel") from None
    return {"status": "ok", "database": "connected"}
