"""Public account payloads. Password hashes and session tokens never appear here."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator, model_validator

from app.passwords import normalize_email, validate_password


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class UserCreate(AccountInput):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=100)
    password: SecretStr
    role: Literal["admin", "viewer"] = "viewer"

    @field_validator("email")
    @classmethod
    def email_valid(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("name")
    @classmethod
    def name_valid(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Informe o nome da pessoa.")
        return value

    @field_validator("password")
    @classmethod
    def password_valid(cls, value: SecretStr) -> SecretStr:
        validate_password(value.get_secret_value())
        return value


class UserUpdate(AccountInput):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: Literal["admin", "viewer"] | None = None
    is_active: StrictBool | None = None

    @field_validator("name")
    @classmethod
    def name_valid(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Informe o nome da pessoa.")
        return value

    @model_validator(mode="after")
    def fields_present(self):
        if not self.model_fields_set:
            raise ValueError("Informe ao menos uma alteracao.")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Os campos informados nao podem ser nulos.")
        return self


class PasswordReset(AccountInput):
    password: SecretStr

    @field_validator("password")
    @classmethod
    def password_valid(cls, value: SecretStr) -> SecretStr:
        validate_password(value.get_secret_value())
        return value


class PasswordChange(AccountInput):
    current_password: SecretStr
    new_password: SecretStr

    @field_validator("current_password")
    @classmethod
    def current_password_valid(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Informe sua senha atual.")
        return value

    @field_validator("new_password")
    @classmethod
    def password_valid(cls, value: SecretStr) -> SecretStr:
        validate_password(value.get_secret_value())
        return value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    role: Literal["admin", "viewer"]
    is_active: bool
    created_at: datetime


class UserPage(BaseModel):
    items: list[UserRead]
    total: int
    limit: int
    offset: int


class PasswordResult(BaseModel):
    message: str
    reauthenticate: bool
