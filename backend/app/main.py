from fastapi import FastAPI

app = FastAPI(
    title="Monitoramento de Pragas em Acelga",
    version="0.1.0",
)


@app.get("/api/v1/health", tags=["Sistema"])
def health() -> dict[str, str]:
    return {"status": "ok"}
