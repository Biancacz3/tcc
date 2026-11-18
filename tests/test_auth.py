"""Auth integration with real SQLAlchemy + FastAPI, SQLite isolation, no network."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "tests")
os.environ.setdefault("POSTGRES_USER", "tests")
os.environ.setdefault("POSTGRES_PASSWORD", "tests-only")

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import router
from app.db.session import get_session
from app.models.user import LoginAttempt, User, UserSession
from app.passwords import hash_password, verify_password
from app.security import SESSION_COOKIE, require_admin, require_user


class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "senha-de-teste-123456"
        cls.hashed = hash_password(cls.password)

    def setUp(self):
        self.env = patch.dict(os.environ, {"ALLOW_LEGACY_ADMIN_TOKEN": "false", "SESSION_COOKIE_SECURE": "false", "APP_ORIGIN": "", "SESSION_DAYS": "7"})
        self.env.start()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        for model in (User, UserSession, LoginAttempt):
            model.__table__.create(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as session:
            session.add(User(email="bianca@example.com", name="Bianca", role="admin", password_hash=self.hashed))
            session.add(User(email="consulta@example.com", name="Consulta", role="viewer", password_hash=self.hashed))
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        @app.get("/api/v1/private", dependencies=[Depends(require_user)])
        def private(): return {"ok": True}
        @app.post("/api/v1/admin", dependencies=[Depends(require_admin)])
        def admin(): return {"ok": True}
        def get_test_session():
            with self.Session() as session:
                yield session
        app.dependency_overrides[get_session] = get_test_session
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.env.stop()

    def login(self, email="bianca@example.com"):
        return self.client.post("/api/v1/auth/login", json={"email": email, "password": self.password}, headers={"Origin": "http://testserver"})

    def test_password_is_salted_and_wrong_password_fails(self):
        self.assertNotEqual(self.hashed, hash_password(self.password))
        self.assertTrue(verify_password(self.password, self.hashed))
        self.assertFalse(verify_password("errada", self.hashed))
        self.assertFalse(verify_password(self.password, "scrypt$9999999999$8$3$00$00"))

    def test_login_cookie_survives_refresh_but_logout_revokes_session(self):
        self.assertEqual(self.client.get("/api/v1/private").status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 200, response.text)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("max-age=604800", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertEqual(self.client.get("/api/v1/auth/me").json()["user"]["email"], "bianca@example.com")
        raw_token = self.client.cookies.get(SESSION_COOKIE)
        with self.Session() as session:
            record = session.scalar(select(UserSession))
            self.assertNotEqual(record.token_hash, raw_token)
        headers = {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]}
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers=headers).status_code, 204)
        self.client.cookies.set(SESSION_COOKIE, raw_token, path="/api/v1")
        self.assertEqual(self.client.get("/api/v1/private").status_code, 401)

    def test_origin_and_csrf_are_both_required_for_cookie_mutation(self):
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"email": "bianca@example.com", "password": self.password}, headers={"Origin": "https://other.example"}).status_code, 403)
        response = self.login()
        token = response.json()["csrf_token"]
        self.assertEqual(self.client.post("/api/v1/admin", headers={"Origin": "http://testserver"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/admin", headers={"Origin": "https://other.example", "X-CSRF-Token": token}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/admin", headers={"Origin": "http://testserver", "X-CSRF-Token": token}).status_code, 200)

    def test_viewer_cannot_mutate_admin_and_expiry_rejects(self):
        response = self.login("consulta@example.com")
        self.assertEqual(self.client.get("/api/v1/private").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/admin", headers={"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]}).status_code, 403)
        with self.Session.begin() as session:
            record = session.scalar(select(UserSession))
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_login_rate_limit_persists_and_uses_generic_failures(self):
        with patch("app.api.auth.verify_password", return_value=False):
            for _ in range(10):
                self.assertEqual(self.login().status_code, 401)
            self.assertEqual(self.login().status_code, 429)
        with self.Session() as session:
            self.assertEqual(len(session.scalars(select(LoginAttempt)).all()), 2)

    def test_legacy_bearer_requires_explicit_flag(self):
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": "a" * 64}):
            headers = {"Authorization": "Bearer " + "a" * 64}
            self.assertEqual(self.client.get("/api/v1/private", headers=headers).status_code, 401)
            with patch.dict(os.environ, {"ALLOW_LEGACY_ADMIN_TOKEN": "true"}):
                self.assertEqual(self.client.post("/api/v1/admin", headers=headers).status_code, 200)


if __name__ == "__main__":
    unittest.main()
