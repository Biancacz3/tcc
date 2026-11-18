"""Account UI contract exercised through real sessions, database and HTTP routes."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "tests")
os.environ.setdefault("POSTGRES_USER", "tests")
os.environ.setdefault("POSTGRES_PASSWORD", "tests-only")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import router as auth_router
from app.api.users import _lock_accounts, router
from app.db.session import get_session
from app.models.user import LoginAttempt, User, UserSession
from app.passwords import hash_password, verify_password
from app.security import Identity


class UserAdministrationTests(unittest.TestCase):
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
            self.admin = User(email="bianca@example.com", name="Bianca", role="admin", password_hash=self.hashed)
            self.viewer = User(email="consulta@example.com", name="Consulta", role="viewer", password_hash=self.hashed)
            session.add_all([self.admin, self.viewer])
        self.app = FastAPI()
        self.app.include_router(auth_router, prefix="/api/v1")
        self.app.include_router(router, prefix="/api/v1")
        def get_test_session():
            with self.Session() as session:
                yield session
        self.app.dependency_overrides[get_session] = get_test_session
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.env.stop()

    def login(self, email="bianca@example.com", *, client=None, password=None):
        client = client or self.client
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password or self.password},
                               headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]}

    def test_anonymous_and_viewer_cannot_administer_accounts(self):
        self.assertEqual(self.client.get("/api/v1/users").status_code, 401)
        headers = self.login("consulta@example.com")
        self.assertEqual(self.client.get("/api/v1/users").status_code, 403)
        self.assertEqual(self.client.post("/api/v1/users", json={"email": "nova@example.com", "name": "Nova", "password": self.password}, headers=headers).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/v1/users/{self.viewer.id}", json={"role": "admin"}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/users/{self.admin.id}/reset-password", json={"password": self.password}, headers=headers).status_code, 403)

    def test_create_normalize_unique_and_public_paginated_payload(self):
        headers = self.login()
        response = self.client.post("/api/v1/users", json={"email": "  NOVA@example.com  ", "name": "  Nova pessoa ", "password": self.password}, headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual((result["email"], result["name"], result["role"]), ("nova@example.com", "Nova pessoa", "viewer"))
        self.assertNotIn("password", response.text)
        self.assertNotIn(self.hashed, response.text)
        with self.Session() as session:
            user = session.scalar(select(User).where(User.email == "nova@example.com"))
            self.assertNotEqual(user.password_hash, self.password)
            self.assertTrue(verify_password(self.password, user.password_hash))
        duplicate = self.client.post("/api/v1/users", json={"email": "Nova@example.com", "name": "Outra", "password": self.password}, headers=headers)
        self.assertEqual(duplicate.status_code, 409)
        page = self.client.get("/api/v1/users?limit=1&offset=1").json()
        self.assertEqual((page["total"], page["limit"], page["offset"], len(page["items"])), (3, 1, 1, 1))
        self.assertEqual(set(page["items"][0]), {"id", "email", "name", "role", "is_active", "created_at"})

    def test_mutations_require_origin_and_csrf(self):
        headers = self.login()
        route = f"/api/v1/users/{self.viewer.id}"
        self.assertEqual(self.client.patch(route, json={"name": "Nova"}).status_code, 403)
        self.assertEqual(self.client.patch(route, json={"name": "Nova"}, headers={"Origin": "http://testserver"}).status_code, 403)
        self.assertEqual(self.client.patch(route, json={"name": "Nova"}, headers={**headers, "Origin": "http://evil.test"}).status_code, 403)
        self.assertEqual(self.client.patch(route, json={"name": "Nova"}, headers=headers).status_code, 200)

    def test_invalid_payloads_do_not_change_accounts(self):
        headers = self.login()
        for payload in ({}, {"name": " "}, {"name": None}, {"email": "other@example.com"}, {"role": "owner"}, {"is_active": "false"}):
            self.assertEqual(self.client.patch(f"/api/v1/users/{self.viewer.id}", json=payload, headers=headers).status_code, 422)
        for extra in ({"password": "curta"}, {"name": " "}, {"email": "invalido"}, {"role": "owner"}):
            payload = {"email": "nova@example.com", "name": "Nova", "password": self.password, **extra}
            self.assertEqual(self.client.post("/api/v1/users", json=payload, headers=headers).status_code, 422)
        with self.Session() as session:
            self.assertTrue(session.get(User, self.viewer.id).is_active)
            self.assertEqual(len(session.scalars(select(User)).all()), 2)

    def test_cannot_remove_last_admin_or_demote_self_even_with_another_admin(self):
        headers = self.login()
        route = f"/api/v1/users/{self.admin.id}"
        for changes in ({"role": "viewer"}, {"is_active": False}):
            response = self.client.patch(route, json=changes, headers=headers)
            self.assertEqual(response.status_code, 409)
            self.assertIn("pelo menos uma", response.text)
        with self.Session.begin() as session:
            session.add(User(email="outro@example.com", name="Outro", role="admin", password_hash=self.hashed))
        response = self.client.patch(route, json={"role": "viewer"}, headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Outra administradora", response.text)

    def test_deactivate_and_role_change_revoke_target_sessions(self):
        with TestClient(self.app) as viewer_client:
            self.login("consulta@example.com", client=viewer_client)
            headers = self.login()
            response = self.client.patch(f"/api/v1/users/{self.viewer.id}", json={"is_active": False}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["is_active"])
            self.assertEqual(viewer_client.get("/api/v1/auth/me").status_code, 401)
            self.assertEqual(self.client.patch(f"/api/v1/users/{self.viewer.id}", json={"is_active": True}, headers=headers).status_code, 200)
            self.assertEqual(viewer_client.get("/api/v1/auth/me").status_code, 401)
            self.login("consulta@example.com", client=viewer_client)
            response = self.client.patch(f"/api/v1/users/{self.viewer.id}", json={"role": "admin"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(viewer_client.get("/api/v1/auth/me").status_code, 401)

    def test_admin_resets_other_password_and_does_not_reactivate_disabled_account(self):
        new_password = "nova-senha-completa-123"
        with TestClient(self.app) as viewer_client:
            self.login("consulta@example.com", client=viewer_client)
            headers = self.login()
            response = self.client.post(f"/api/v1/users/{self.viewer.id}/reset-password", json={"password": new_password}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["reauthenticate"])
            self.assertNotIn(new_password, response.text)
            self.assertEqual(viewer_client.get("/api/v1/auth/me").status_code, 401)
            self.login("consulta@example.com", client=viewer_client, password=new_password)
            self.assertEqual(self.client.post(f"/api/v1/users/{self.admin.id}/reset-password", json={"password": new_password}, headers=headers).status_code, 409)
            self.client.patch(f"/api/v1/users/{self.viewer.id}", json={"is_active": False}, headers=headers)
            self.assertEqual(self.client.post(f"/api/v1/users/{self.viewer.id}/reset-password", json={"password": self.password}, headers=headers).status_code, 200)
            with self.Session() as session:
                self.assertFalse(session.get(User, self.viewer.id).is_active)

    def test_own_password_requires_current_password_and_revokes_all_sessions(self):
        new_password = "nova-senha-propria-123"
        with TestClient(self.app) as another_client:
            self.login(client=another_client)
            headers = self.login()
            self.assertEqual(self.client.post("/api/v1/users/me/password", json={"current_password": "errada", "new_password": new_password}, headers=headers).status_code, 400)
            self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)
            response = self.client.post("/api/v1/users/me/password", json={"current_password": self.password, "new_password": new_password}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["reauthenticate"])
            self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
            self.assertEqual(another_client.get("/api/v1/auth/me").status_code, 401)
            self.login(password=new_password)

    def test_viewer_can_change_own_password(self):
        headers = self.login("consulta@example.com")
        response = self.client.post("/api/v1/users/me/password", json={"current_password": self.password, "new_password": "nova-senha-consulta-456"}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)

    def test_invalid_password_payloads_never_echo_secrets(self):
        from fastapi.exceptions import RequestValidationError
        from app.main import invalid_request

        # Exercise the production handler, including the existing login route.
        self.app.add_exception_handler(RequestValidationError, invalid_request)
        headers = self.login()
        short_secret = "segredo-X"
        long_secret = "nao-ecoar-esta-senha-" * 10
        cases = [
            ("/api/v1/auth/login", {"email": self.admin.email, "password": long_secret}),
            ("/api/v1/users", {"email": "nova@example.com", "name": "Nova", "password": short_secret}),
            ("/api/v1/users/me/password", {"current_password": self.password, "new_password": short_secret}),
            (f"/api/v1/users/{self.viewer.id}/reset-password", {"password": short_secret}),
        ]
        for route, payload in cases:
            response = self.client.post(route, json=payload, headers=headers)
            self.assertEqual(response.status_code, 422, response.text)
            for secret in (short_secret, long_secret, self.password):
                self.assertNotIn(secret, response.text)
            for error in response.json()["detail"]:
                self.assertEqual(set(error), {"loc", "msg", "type"})

    def test_waiting_request_refreshes_a_demoted_admin_before_mutation(self):
        self.login()
        # Simulate a request authenticated before another administrator demotes
        # it. The mutation guard must refresh cached ORM state after its lock.
        with self.Session() as waiting:
            user = waiting.get(User, self.admin.id)
            record = waiting.scalar(select(UserSession).where(UserSession.user_id == user.id))
            identity = Identity(user=user, session=record, role="admin")
            with self.Session.begin() as other:
                other.execute(update(User).where(User.id == user.id).values(role="viewer"))
            with self.assertRaises(HTTPException) as error:
                _lock_accounts(waiting, identity, admin=True)
            self.assertEqual(error.exception.status_code, 403)
            waiting.rollback()


if __name__ == "__main__":
    unittest.main()
