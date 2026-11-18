"""Upload -> fila -> worker -> historico. Usa SQLite e inferencia falsa deterministica."""
import hashlib
import os
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

os.environ.update(ADMIN_API_TOKEN="a" * 64, ALLOW_LEGACY_ADMIN_TOKEN="true",
    DB_HOST="localhost", POSTGRES_DB="test", POSTGRES_USER="test", POSTGRES_PASSWORD="test")

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.api import captures
from app.db.session import get_session
from app.jobs import claim, MAX_ATTEMPTS
from app.models import Base, Capture, Device, Analysis, AnalysisJob
from app.security import hash_device_token
from app.worker import run_once

ADMIN = {"Authorization": "Bearer " + "a" * 64}
DEVICE_KEY = "test-camera-credential"


class FakeDetector:
    sha256 = "b" * 64
    configuration_sha256 = "c" * 64
    calls = 0

    def predict(self, rgb):
        self.calls += 1
        return [{"label": "pulgao", "confidence": 0.9, "xyxy": [10, 10, 20, 20]}]


@pytest.fixture
def setup(tmp_path, monkeypatch):
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
    with factory() as session:
        device = Device(id=uuid4(), name="Raspberry01", is_active=True,
            api_token_hash=hash_device_token(DEVICE_KEY))
        session.add(device)
        session.commit()
        device_id = device.id
    def dependency():
        with factory() as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    monkeypatch.setattr(captures, "STORAGE_DIR", tmp_path)
    monkeypatch.setenv("ALLOW_LEGACY_ADMIN_TOKEN", "true")
    image = BytesIO()
    Image.new("RGB", (100, 80), (40, 120, 20)).save(image, format="JPEG")
    with TestClient(app) as client:
        yield client, factory, tmp_path, image.getvalue(), device_id
    app.dependency_overrides.clear()
    engine.dispose()


def upload(setup, identifier=None, **metadata):
    client, _, _, data, _ = setup
    headers = {"Authorization": "Bearer " + DEVICE_KEY, "Content-Type": "image/jpeg",
        "X-Capture-Id": str(identifier or uuid4()), "X-Captured-At": "2026-10-26T12:00:00+00:00"}
    headers.update(metadata)
    return client.post("/api/v1/captures", content=data, headers=headers)


def test_upload_queues_atomic_and_worker_runs_without_click(setup):
    client, factory, directory, data, _ = setup
    response = upload(setup, **{"X-Plant-Id": "aruco-10", "X-Plant-Name": "Acelga 01", "X-Marker-Id": "10"})
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["analysis_status"] == "queued" and item["detection_count"] is None
    detector = FakeDetector()
    assert run_once(factory, lambda: detector, directory)
    assert detector.calls == 1
    assert not run_once(factory, lambda: detector, directory)
    job = client.get(f"/api/v1/captures/{item['id']}/analysis-job", headers=ADMIN).json()
    assert job["status"] == "completed" and job["attempts"] == 1 and job["analysis_id"]
    analysis = client.get(f"/api/v1/captures/{item['id']}/analyses", headers=ADMIN).json()[0]
    assert analysis["display_status"] == "Praga detectada" and analysis["model_scope"] == "pulgao"
    assert analysis["detections"][0]["label"] == "pulgao"
    listing = client.get("/api/v1/captures", headers=ADMIN).json()
    assert listing[0]["detection_count"] == 1 and listing[0]["analysis_status"] == "completed"
    history = client.get("/api/v1/plants/positive-history", headers=ADMIN).json()
    assert history["total"] == 1 and history["items"][0]["plant_name"] == "Acelga 01"
    assert (directory / str(setup[4]) / (item["id"] + ".jpg")).read_bytes() == data


def test_replayed_upload_preserves_queue_and_rejects_changed_plant(setup):
    client, factory, directory, *_ = setup
    identifier = uuid4()
    assert upload(setup, identifier, **{"X-Plant-Id": "aruco-10"}).status_code == 201
    assert upload(setup, identifier, **{"X-Plant-Id": "aruco-10"}).status_code == 200
    assert upload(setup, identifier, **{"X-Plant-Id": "aruco-11"}).status_code == 409
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Capture)) == 1
        assert session.scalar(select(func.count()).select_from(AnalysisJob)) == 1
    detector = FakeDetector()
    run_once(factory, lambda: detector, directory)
    assert upload(setup, identifier, **{"X-Plant-Id": "aruco-10"}).status_code == 200
    assert not run_once(factory, lambda: detector, directory)


def test_replay_after_client_upgrade_enriches_only_missing_metadata(setup):
    client, factory, directory, *_ = setup
    identifier = uuid4()
    assert upload(setup, identifier).status_code == 201
    enriched = upload(setup, identifier, **{"X-Plant-Id": "aruco-10", "X-Plant-Name": "Acelga 01"})
    assert enriched.status_code == 200 and enriched.json()["plant_name"] == "Acelga 01"
    # Cliente antigo reenviado ainda e aceito, sem apagar identidade ja conhecida.
    assert upload(setup, identifier).json()["plant_id"] == "aruco-10"
    assert upload(setup, identifier, **{"X-Plant-Name": "Outra planta"}).status_code == 409


def test_failed_model_retries_persistently_then_can_be_requeued(setup):
    client, factory, directory, *_ = setup
    item = upload(setup).json()
    def broken():
        raise FileNotFoundError("no model")
    for i in range(MAX_ATTEMPTS):
        assert run_once(factory, broken, directory)
        with factory() as session:
            job = session.scalar(select(AnalysisJob))
            assert job.attempts == i + 1
            assert job.status == ("failed" if i == MAX_ATTEMPTS - 1 else "retry")
            job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
    assert not run_once(factory, broken, directory)
    response = client.post(f"/api/v1/captures/{item['id']}/analysis-job/retry", headers=ADMIN)
    assert response.status_code == 200 and response.json()["attempts"] == 0
    assert run_once(factory, FakeDetector, directory)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Analysis)) == 1


def test_worker_recovers_expired_lease_and_existing_analysis_not_duplicated(setup):
    _, factory, directory, *_ = setup
    upload(setup)
    with factory() as session:
        old = datetime.now(UTC) - timedelta(minutes=10)
        job = session.scalar(select(AnalysisJob))
        job.next_attempt_at = old
        session.commit()
        first = claim(session, now=old)
    detector = FakeDetector()
    assert run_once(factory, lambda: detector, directory)
    with factory() as session:
        job = session.scalar(select(AnalysisJob))
        assert job.status == "completed" and job.attempts == 2
        job.status = "queued"
        job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert run_once(factory, lambda: detector, directory)
    assert detector.calls == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Analysis)) == 1


def test_read_permissions_and_historical_filters(setup):
    client, factory, directory, *_ = setup
    first = upload(setup, **{"X-Plant-Id": "aruco-10"}).json()
    second = upload(setup).json()
    run_once(factory, FakeDetector, directory)
    run_once(factory, FakeDetector, directory)
    for path in ("/api/v1/plants/positive-history", f"/api/v1/captures/{first['id']}",
                 f"/api/v1/captures/{first['id']}/analysis-job"):
        assert client.get(path).status_code == 401
    page = client.get("/api/v1/plants/positive-history?limit=1&offset=1", headers=ADMIN).json()
    assert page.get("total") == 2 and len(page["items"]) == 1, page
    assert client.get("/api/v1/plants/positive-history?plant_id=aruco-10", headers=ADMIN).json()["total"] == 1
    assert client.get("/api/v1/plants/positive-history?from_date=2026-10-27", headers=ADMIN).json()["total"] == 0
    assert client.get("/api/v1/plants/positive-history?from_date=2026-10-27&to_date=2026-10-26", headers=ADMIN).status_code == 422
    data = client.get(f"/api/v1/captures/{second['id']}", headers=ADMIN).json()
    assert data["plant_id"] is None and data["plant_name"] is None
