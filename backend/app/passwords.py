"""Password hashing with scrypt; no plaintext passwords are stored."""
import hashlib
import re
import secrets

N, R, P = 2 ** 15, 8, 3
MAXMEM = 64 * 1024 * 1024


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Informe um e-mail valido.")
    return value


def validate_password(password: str) -> None:
    if not 12 <= len(password) <= 128:
        raise ValueError("Use uma senha de 12 a 128 caracteres.")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P,
                            maxmem=MAXMEM, dklen=32)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        method, n, r, p, salt_hex, digest_hex = encoded.split("$")
        # Fixed allowlist also prevents corrupted database values from causing
        # excessive memory allocation during login.
        if (method, int(n), int(r), int(p)) != ("scrypt", N, R, P):
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P,
                                maxmem=MAXMEM, dklen=32)
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# Unknown/inactive accounts still perform the same expensive hash operation.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
