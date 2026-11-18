"""Fluxo HTTP/worker com ONNX real e duas fotografias de desenvolvimento.

Opcional: TCC_TEST_MODEL aponta para pulgao-v1.onnx; TCC_TEST_PHOTOS aponta
para a pasta fotos-teste do pacote (foto-001.jpg e foto-002-marcada.jpg).
TCC_TEST_EVIDENCE pode indicar o JSON de evidencia a salvar, sem credenciais.
Executar: PYTHONPATH=backend python -m pytest tests/test_real_pipeline.py -q

SQLite e armazenamento sao temporarios. Nao usa rede, camera ou motores.
Os valores esperados verificam apenas estas fotos; nao estimam generalizacao.
As anotacoes visuais manuais nao sao carregadas por este teste.
"""
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

if not os.environ.get("TCC_TEST_MODEL") or not os.environ.get("TCC_TEST_PHOTOS"):
    pytest.skip("Defina TCC_TEST_MODEL e TCC_TEST_PHOTOS para a verificacao ONNX real.", allow_module_level=True)

for key, value in {"DB_HOST": "localhost", "POSTGRES_DB": "test", "POSTGRES_USER": "test", "POSTGRES_PASSWORD": "test-only"}.items():
    os.environ.setdefault(key, value)

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import captures
from app.db.session import get_session
from app.inference import Detector
from app.main import app
from app.models import Base, Capture, Analysis
from app.models.user import User
from app.passwords import hash_password
from app.worker import run_once


PHOTO_CASES = (
    ("foto-001.jpg", "fd9bbe48183408e9de783d8872a5a690f8093e7f5baaeced2a77da90a292eccb", 7, 8, 1),
    ("foto-002-marcada.jpg", "b04fb62e1164738bc78b18a8bc3d519cfb344c23736a31232db3827224f1dda7", 3, 6, 3),
)


def test_real_admin_upload_queue_worker_and_history(tmp_path, monkeypatch):
    model = Path(os.environ["TCC_TEST_MODEL"])
    photos = Path(os.environ["TCC_TEST_PHOTOS"])
    assert model.is_file() and photos.is_dir()
    detector = Detector(model)
    assert detector.sha256 == "cde9133603b04fab3f0f6f1a244e19adbebb398ce4a3900e51f0c252a234fb60"
    monkeypatch.setenv("ALLOW_LEGACY_ADMIN_TOKEN", "false")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("APP_ORIGIN", "")
    monkeypatch.setenv("SESSION_DAYS", "7")
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(captures, "STORAGE_DIR", storage)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def connect(db, _):
        db.create_function("now", 0, lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"))
        db.execute("PRAGMA foreign_keys=ON")
        db.isolation_level = None

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    password = uuid4().hex
    with factory.begin() as session:
        session.add(User(email="e2e@example.test", name="Teste local", role="admin", password_hash=hash_password(password)))

    def dependency():
        with factory() as session:
            yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = dependency
    evidence = {"checked_at": datetime.now(UTC).isoformat(), "test": "real_session_upload_worker_history",
                "database": "SQLite temporario", "api_transport": "FastAPI TestClient, sem rede",
                "model_sha256": detector.sha256, "configuration_sha256": detector.configuration_sha256,
                "model_scope": "pulgao", "manual_annotations_used": False,
                "limitations": ["Duas fotos de desenvolvimento com simulacoes; nao e validacao independente.",
                                "A segunda fotografia ja possui desenhos de diagnostico.",
                                "Nao avalia a rejeicao de tampas, pedras ou outros objetos."],
                "captures": []}
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/captures").status_code == 401
            login = client.post("/api/v1/auth/login", json={"email": "e2e@example.test", "password": password},
                                headers={"Origin": "http://testserver"})
            assert login.status_code == 200, login.text
            auth = {"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"]}
            assert client.get("/api/v1/auth/me").json()["user"]["role"] == "admin"
            for index, (name, expected_hash, detections, candidates_count, unidentified) in enumerate(PHOTO_CASES):
                data = (photos / name).read_bytes()
                original_hash = hashlib.sha256(data).hexdigest()
                assert original_hash == expected_hash, "Use exatamente as duas fotos selecionadas."
                headers = {**auth, "Content-Type": "image/jpeg", "X-Capture-Id": str(uuid4()),
                           "X-Captured-At": f"2026-10-26T12:0{index}:00+00:00", "X-Marker-Id": "10",
                           "X-Plant-Id": "aruco-10", "X-Plant-Name": "Acelga 01"}
                without_csrf = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
                assert client.post("/api/v1/captures/manual", content=data, headers=without_csrf).status_code == 403
                response = client.post("/api/v1/captures/manual", content=data, headers=headers)
                assert response.status_code == 201, response.text
                item = response.json()
                assert item["analysis_status"] == "queued" and item["source"] == "manual"
                # O envio cria a fila; nao existe chamada ao botao/end-point de analisar.
                assert run_once(factory, lambda: detector, storage)
                assert not run_once(factory, lambda: detector, storage)
                base = f"/api/v1/captures/{item['id']}"
                job = client.get(base + "/analysis-job").json()
                assert job["status"] == "completed" and job["attempts"] == 1, job
                detail = client.get(base).json()
                assert detail["analysis_status"] == "completed" and detail["analysis_state"] == "positive", detail
                analyses = client.get(base + "/analyses").json()
                assert len(analyses) == 1
                analysis = analyses[0]
                assert analysis["analysis_state"] == "positive"
                assert analysis["detection_count"] == detections
                assert analysis["candidate_count"] == candidates_count
                assert analysis["unidentified_count"] == unidentified
                assert analysis["display_status"] == "Praga detectada" and analysis["model_scope"] == "pulgao"
                assert analysis["image_sha256"] == original_hash
                assert (analysis["width"], analysis["height"]) == (2048, 1152)
                image_response = client.get(base + "/image")
                assert image_response.status_code == 200 and image_response.content == data
                with factory() as session:
                    capture_row = session.get(Capture, UUID(item["id"]))
                    analysis_row = session.get(Analysis, UUID(analysis["id"]))
                    assert capture_row.sha256 == original_hash
                    assert (storage / capture_row.storage_key).read_bytes() == data
                    assert analysis_row.configuration_sha256 == detector.configuration_sha256
                evidence["captures"].append({"source": name, "original_sha256": original_hash,
                    "upload_http_status": response.status_code, "upload_job_state": item["analysis_status"],
                    "original_bytes_preserved_in_storage": True, "original_bytes_preserved_in_download": True,
                    "job_state": job["status"], "job_attempts": job["attempts"],
                    "capture_state": detail["analysis_state"], "analysis": analysis})
            listing = client.get("/api/v1/captures").json()
            assert len(listing) == 2 and all(item["analysis_state"] == "positive" for item in listing)
            assert sorted(item["detection_count"] for item in listing) == [3, 7]
            summary = client.get("/api/v1/dashboard/summary").json()
            assert summary["captures"] == summary["positive_captures"] == 2
            assert summary["review_captures"] == 2  # Positive photos also have unidentified regions.
            assert summary["queued_captures"] == summary["failed_captures"] == 0
            history = client.get("/api/v1/plants/positive-history?plant_id=aruco-10").json()
            assert history["total"] == 2 and history["scope"] == "historical_positive"
            assert sorted(item["detection_count"] for item in history["items"]) == [3, 7]
            assert all(item["model_labels"] == ["pulgao"] for item in history["items"])
            evidence.update(status="passed", session_login_verified=True, csrf_protection_verified=True,
                            dashboard=summary, history=history)
            output = os.environ.get("TCC_TEST_EVIDENCE")
            if output:
                path = Path(output)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()
