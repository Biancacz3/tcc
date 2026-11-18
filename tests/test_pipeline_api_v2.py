"""HTTP/worker integration for manual provenance, region results and history."""
import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select

from test_jobs_plants import setup, upload, ADMIN, DEVICE_KEY, FakeDetector
from app.models import Analysis, AnalysisJob, Capture, Device
from app.models.user import User, UserSession
from app.provenance import MANUAL_DEVICE_ID
from app.security import SESSION_COOKIE, hash_device_token
from app.worker import run_once


class RegionDetector:
    sha256 = "d" * 64
    configuration_sha256 = "e" * 64

    def __init__(self, state="review"):
        self.state = state
        self.calls = []

    def analyze(self, rgb, marker_id=None):
        self.calls.append((rgb.shape, marker_id))
        detections = ([{"label": "pulgao", "confidence": .85, "xyxy": [10, 10, 20, 20]}]
                      if self.state == "positive" else [])
        candidates = ([{"id": 0, "xyxy": [10, 10, 20, 20],
                       "status": "detected" if detections else "unidentified"}]
                      if self.state in ("positive", "review") else [])
        return dict(status=self.state, detections=detections, candidates=candidates,
                    warnings=["Regiao experimental; revisar visualmente."],
                    method="leaf-regions-test", plant_bbox=[5, 5, 60, 60])


def session_headers(setup, role="admin"):
    client, factory, *_ = setup
    token, csrf = uuid4().hex, uuid4().hex
    with factory.begin() as session:
        user = User(email=f"{uuid4().hex}@example.com", name="Bianca", role=role, password_hash="unused")
        session.add(user)
        session.flush()
        session.add(UserSession(token_hash=hash_device_token(token), user_id=user.id,
                                csrf_token=csrf, expires_at=datetime.now(UTC) + timedelta(hours=1)))
    client.cookies.set(SESSION_COOKIE, token, path="/api/v1")
    return {"Origin": "http://testserver", "X-CSRF-Token": csrf}


def manual_headers(auth, identifier=None):
    return {**auth, "Content-Type": "image/jpeg", "X-Capture-Id": str(identifier or uuid4()),
            "X-Captured-At": "2026-10-26T12:00:00+00:00", "X-Marker-Id": "10"}


def test_manual_upload_is_csrf_protected_distinct_and_idempotent(setup):
    client, factory, directory, data, device_id = setup
    auth = session_headers(setup)
    headers = manual_headers(auth)
    missing_csrf = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
    assert client.post("/api/v1/captures/manual", content=data, headers=missing_csrf).status_code == 403
    assert client.post("/api/v1/captures/manual", content=data,
                       headers={**headers, "Origin": "https://other.example"}).status_code == 403
    headers.update({"X-Plant-Id": "aruco-10", "X-Plant-Name": quote("Acelga com revisão"),
                    "X-Plant-Name-Encoding": "percent-utf8"})
    first = client.post("/api/v1/captures/manual", content=data, headers=headers)
    assert first.status_code == 201, first.text
    item = first.json()
    assert item["source"] == "manual" and item["device_id"] != str(device_id)
    assert item["plant_name"] == "Acelga com revisão" and item["analysis_status"] == "queued"
    assert client.post("/api/v1/captures/manual", content=data, headers=headers).status_code == 200
    with factory() as session:
        source = session.get(Device, MANUAL_DEVICE_ID)
        assert source.name == "Envio pelo painel" and source.api_token_hash is None and not source.is_active
        assert session.scalar(select(func.count(Capture.id))) == 1
        assert session.scalar(select(func.count(AnalysisJob.capture_id))) == 1
    assert (directory / str(MANUAL_DEVICE_ID) / f"{item['id']}.jpg").read_bytes() == data
    # Device upload cannot adopt the ID of a manual image.
    assert upload(setup, UUID(item["id"])).status_code == 409


def test_manual_rejects_viewer_device_and_invalid_jpeg_without_writes(setup):
    client, factory, _, data, _ = setup
    viewer = session_headers(setup, "viewer")
    assert client.post("/api/v1/captures/manual", content=data, headers=manual_headers(viewer)).status_code == 403
    assert client.post("/api/v1/captures/manual", content=data,
                       headers=manual_headers({"Authorization": "Bearer " + DEVICE_KEY})).status_code == 401
    admin = session_headers(setup)
    assert client.post("/api/v1/captures/manual", content=b"notjpeg", headers=manual_headers(admin)).status_code == 422
    with factory() as session:
        assert session.get(Device, MANUAL_DEVICE_ID) is None
        assert session.scalar(select(func.count(Capture.id))) == 0


def test_manual_exif_preserves_bytes_and_analysis_uses_display_coordinates(setup):
    client, factory, directory, *_ = setup
    auth = session_headers(setup)
    buffer = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (100, 80), (40, 120, 20)).save(buffer, format="JPEG", exif=exif)
    data = buffer.getvalue()
    response = client.post("/api/v1/captures/manual", content=data, headers=manual_headers(auth))
    assert response.status_code == 201, response.text
    item = response.json()
    detector = RegionDetector()
    assert run_once(factory, lambda: detector, directory)
    assert detector.calls == [((100, 80, 3), 10)]
    analysis = client.get(f"/api/v1/captures/{item['id']}/analyses").json()[0]
    assert (analysis["width"], analysis["height"]) == (80, 100)
    assert analysis["image_sha256"] == hashlib.sha256(data).hexdigest()
    assert analysis["analysis_state"] == "review" and analysis["detection_count"] == 0
    assert analysis["candidate_count"] == analysis["unidentified_count"] == 1
    assert analysis["display_status"] == "Revisar imagem"
    assert client.get(f"/api/v1/captures/{item['id']}/image").content == data


def test_reanalysis_uses_current_configuration_keeps_history_and_cache(setup):
    client, factory, directory, *_ = setup
    item = upload(setup, **{"X-Plant-Id": "aruco-10"}).json()
    assert run_once(factory, FakeDetector, directory)
    auth = session_headers(setup)
    route = f"/api/v1/captures/{item['id']}/reanalyze"
    assert client.post(route).status_code == 403
    assert client.post(route, headers=auth).json()["status"] == "queued"
    assert client.post(route, headers=auth).json()["status"] == "queued"
    detector = RegionDetector()
    assert run_once(factory, lambda: detector, directory)
    analyses = client.get(f"/api/v1/captures/{item['id']}/analyses").json()
    assert len(analyses) == 2 and analyses[0]["analysis_state"] == "review"
    assert analyses[1]["analysis_state"] == "legacy" and analyses[1]["result"] == "possible_aphids"
    listing = client.get("/api/v1/captures").json()[0]
    assert listing["analysis_state"] == "review" and listing["detection_count"] == 0
    history = client.get("/api/v1/plants/positive-history").json()
    assert history["total"] == 1 and history["scope"] == "historical_positive"
    assert history["items"][0]["analysis_state"] == "legacy"
    assert client.post(route, headers=auth).json()["status"] == "queued"
    assert run_once(factory, lambda: detector, directory)
    assert len(detector.calls) == 1  # Same configuration is idempotent.
    assert len(client.get(f"/api/v1/captures/{item['id']}/analyses").json()) == 2


def test_reanalysis_rejects_processing_and_viewer(setup):
    client, factory, *_ = setup
    item = upload(setup).json()
    route = f"/api/v1/captures/{item['id']}/reanalyze"
    auth = session_headers(setup, "viewer")
    assert client.post(route, headers=auth).status_code == 403
    auth = session_headers(setup)
    with factory.begin() as session:
        job = session.get(AnalysisJob, UUID(item["id"]))
        job.status = "processing"
    assert client.post(route, headers=auth).status_code == 409


def test_dashboard_counts_and_filters_use_latest_results(setup):
    client, factory, directory, *_ = setup
    a = upload(setup, **{"X-Plant-Id": "aruco-10"}).json()
    run_once(factory, FakeDetector, directory)
    auth = session_headers(setup)
    client.post(f"/api/v1/captures/{a['id']}/reanalyze", headers=auth)
    run_once(factory, RegionDetector, directory)
    b = upload(setup).json()
    run_once(factory, lambda: RegionDetector("positive"), directory)
    c = upload(setup).json()
    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["captures"] == 3 and summary["positive_captures"] == 1
    assert summary["review_captures"] == 1 and summary["queued_captures"] == 1
    assert summary["devices"] == 1 and summary["last_received_at"]
    assert len(client.get("/api/v1/captures?analysis_state=review").json()) == 1
    assert client.get("/api/v1/captures?analysis_state=pending").json()[0]["id"] == c["id"]
    assert client.get("/api/v1/captures?analysis_state=positive").json()[0]["id"] == b["id"]
    assert client.get("/api/v1/captures?plant_id=aruco-10").json()[0]["id"] == a["id"]
    assert client.get("/api/v1/captures?from_date=2026-10-27").json() == []
    assert client.get("/api/v1/captures?from_date=2026-10-27&to_date=2026-10-26").status_code == 422


def test_unknown_candidates_never_create_positive_history(setup):
    client, factory, directory, *_ = setup
    item = upload(setup).json()
    assert run_once(factory, RegionDetector, directory)
    response = client.get(f"/api/v1/captures/{item['id']}", headers=ADMIN).json()
    assert response["analysis_state"] == "review" and response["detection_count"] == 0
    assert response["unidentified_count"] == 1 and response["candidate_count"] == 1
    assert client.get("/api/v1/plants/positive-history", headers=ADMIN).json()["total"] == 0


def test_positive_photo_with_unknown_region_is_also_pending_visual_review(setup):
    client, factory, directory, *_ = setup
    item = upload(setup).json()
    class MixedDetector(RegionDetector):
        def analyze(self, rgb, marker_id=None):
            result = super().analyze(rgb, marker_id)
            result["candidates"].append({"id": 1, "xyxy": [30, 30, 40, 40], "status": "unidentified"})
            return result
    detector = MixedDetector("positive")
    assert run_once(factory, lambda: detector, directory)
    summary = client.get("/api/v1/dashboard/summary", headers=ADMIN).json()
    assert summary["positive_captures"] == summary["review_captures"] == 1
    review = client.get("/api/v1/captures?analysis_state=review", headers=ADMIN).json()
    assert len(review) == 1 and review[0]["id"] == item["id"]
    assert review[0]["analysis_state"] == "positive" and review[0]["unidentified_count"] == 1
