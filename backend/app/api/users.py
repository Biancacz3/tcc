"""Account administration using the existing same-origin session and CSRF guard."""
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.auth import cookie_secure
from app.db.session import get_session
from app.models.user import User, UserSession
from app.passwords import hash_password, verify_password
from app.schemas.user import PasswordChange, PasswordReset, PasswordResult, UserCreate, UserPage, UserRead, UserUpdate
from app.security import Identity, SESSION_COOKIE, require_admin, require_user

router = APIRouter(prefix="/users", tags=["Contas"])
logger = logging.getLogger(__name__)
DatabaseSession = Annotated[Session, Depends(get_session)]
Administrator = Annotated[Identity, Depends(require_admin)]
AuthenticatedUser = Annotated[Identity, Depends(require_user)]


def _lock_accounts(session: Session, identity: Identity, *, admin: bool) -> dict[UUID, User]:
    # This is a small local system. Lock all account rows in deterministic order
    # so simultaneous administrator removals cannot leave the system with none.
    # Refresh the caller too: its role may have changed while waiting for locks.
    users = {user.id: user for user in session.scalars(
        select(User).order_by(User.id).with_for_update()
        .execution_options(populate_existing=True))}
    if identity.user is None or identity.session is None:
        raise HTTPException(403, "Entre com uma conta individual para gerenciar contas.")
    actor = users.get(identity.user.id)
    live_session = session.scalar(select(UserSession.token_hash).where(
        UserSession.token_hash == identity.session.token_hash,
        UserSession.user_id == identity.user.id))
    if actor is None or not actor.is_active or not live_session:
        raise HTTPException(401, "Sua sessao foi encerrada. Entre novamente.")
    if admin and actor.role != "admin":
        raise HTTPException(403, "Esta operacao exige uma conta administradora.")
    return users


def _target(users: dict[UUID, User], user_id: UUID) -> User:
    user = users.get(user_id)
    if user is None:
        raise HTTPException(404, "Conta nao encontrada.")
    return user


def _database_failure(session: Session) -> HTTPException:
    session.rollback()
    # Do not log ORM exception parameters: an INSERT contains the password hash.
    logger.error("Falha ao persistir ou consultar contas de acesso.")
    return HTTPException(503, "Nao foi possivel acessar as contas. Tente novamente.")


@router.get("", response_model=UserPage)
def list_users(
    session: DatabaseSession,
    identity: Administrator,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    try:
        total = session.scalar(select(func.count(User.id)))
        users = session.scalars(select(User).order_by(User.name, User.id).offset(offset).limit(limit)).all()
        return UserPage(items=[UserRead.model_validate(user) for user in users],
                        total=total, limit=limit, offset=offset)
    except SQLAlchemyError:
        raise _database_failure(session) from None


@router.post("", response_model=UserRead, status_code=201)
def create_user(payload: UserCreate, session: DatabaseSession, identity: Administrator):
    try:
        _lock_accounts(session, identity, admin=True)
        if session.scalar(select(User.id).where(User.email == payload.email)) is not None:
            raise HTTPException(409, "Este e-mail ja possui uma conta.")
        user = User(email=payload.email, name=payload.name, role=payload.role,
                    password_hash=hash_password(payload.password.get_secret_value()), is_active=True)
        session.add(user)
        session.flush()
        result = UserRead.model_validate(user)
        session.commit()
        return result
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Este e-mail ja possui uma conta.") from None
    except SQLAlchemyError:
        raise _database_failure(session) from None


# Declare /me/password before UUID routes to keep the OpenAPI contract explicit.
@router.post("/me/password", response_model=PasswordResult)
def change_own_password(payload: PasswordChange, response: Response,
                        session: DatabaseSession, identity: AuthenticatedUser):
    try:
        users = _lock_accounts(session, identity, admin=False)
        user = users[identity.user.id]
        current, new = payload.current_password.get_secret_value(), payload.new_password.get_secret_value()
        if not verify_password(current, user.password_hash):
            raise HTTPException(400, "Senha atual incorreta.")
        if current == new:
            raise HTTPException(422, "Escolha uma senha diferente da atual.")
        user.password_hash = hash_password(new)
        session.execute(delete(UserSession).where(UserSession.user_id == user.id))
        session.commit()
        response.delete_cookie(SESSION_COOKIE, path="/api/v1", secure=cookie_secure(), httponly=True, samesite="lax")
        response.headers["Cache-Control"] = "no-store"
        return PasswordResult(message="Senha alterada. Entre novamente com a nova senha.", reauthenticate=True)
    except HTTPException:
        session.rollback()
        raise
    except SQLAlchemyError:
        raise _database_failure(session) from None


@router.patch("/{user_id}", response_model=UserRead)
def update_user(user_id: UUID, payload: UserUpdate, session: DatabaseSession, identity: Administrator):
    try:
        users = _lock_accounts(session, identity, admin=True)
        user = _target(users, user_id)
        changes = payload.model_dump(exclude_unset=True)
        next_role = changes.get("role", user.role)
        next_active = changes.get("is_active", user.is_active)
        removing_admin = user.role == "admin" and user.is_active and (next_role != "admin" or not next_active)
        active_admins = sum(account.role == "admin" and account.is_active for account in users.values())
        if removing_admin and active_admins <= 1:
            raise HTTPException(409, "Mantenha pelo menos uma conta administradora ativa.")
        if user.id == identity.user.id and (next_role != "admin" or not next_active):
            raise HTTPException(409, "Outra administradora deve alterar seu perfil ou desativar sua conta.")
        revoke = user.role != next_role or user.is_active != next_active
        for name, value in changes.items():
            setattr(user, name, value)
        if revoke:
            session.execute(delete(UserSession).where(UserSession.user_id == user.id))
        session.flush()
        result = UserRead.model_validate(user)
        session.commit()
        return result
    except HTTPException:
        session.rollback()
        raise
    except SQLAlchemyError:
        raise _database_failure(session) from None


@router.post("/{user_id}/reset-password", response_model=PasswordResult)
def reset_password(user_id: UUID, payload: PasswordReset, session: DatabaseSession, identity: Administrator):
    try:
        users = _lock_accounts(session, identity, admin=True)
        user = _target(users, user_id)
        if user.id == identity.user.id:
            raise HTTPException(409, "Use Alterar minha senha para atualizar sua propria conta.")
        user.password_hash = hash_password(payload.password.get_secret_value())
        session.execute(delete(UserSession).where(UserSession.user_id == user.id))
        session.commit()
        return PasswordResult(message="Senha redefinida. As sessoes anteriores dessa conta foram encerradas.",
                              reauthenticate=False)
    except HTTPException:
        session.rollback()
        raise
    except SQLAlchemyError:
        raise _database_failure(session) from None
