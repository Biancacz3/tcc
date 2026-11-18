import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.captures import router as captures_router
from app.api.analyses import router as analyses_router
from app.api.devices import router as devices_router
from app.api.analysis_jobs import router as analysis_jobs_router
from app.api.plants import router as plants_router
from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.api.dashboard import router as dashboard_router
from app.db.engine import engine


logger = logging.getLogger(__name__)

app = FastAPI(
    title="Monitoramento de Pragas em Acelga",
    version="0.2.0",
)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Pydantic includes invalid input by default, including password fields.
    # Return only field paths and descriptions; never echo request values.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]})

app.include_router(devices_router, prefix="/api/v1")
app.include_router(captures_router, prefix="/api/v1")
app.include_router(analyses_router, prefix="/api/v1")
app.include_router(analysis_jobs_router, prefix="/api/v1")
app.include_router(plants_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(dashboard_router, prefix="/api/v1")

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
