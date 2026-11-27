from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Mapping

PLATFORM_ADMIN_EMAIL = "stefan.hermes@htcglobal.asia"
PBKDF2_ITERATIONS = 600_000

# A customer's own password, not one of ours. Twelve characters and three of the four kinds of
# character: long enough to be worth the 600,000 PBKDF2 rounds above, short enough that somebody
# in a purchasing office will actually pick one rather than write it on the monitor.
MINIMUM_PASSWORD_LENGTH = 12


@dataclass(frozen=True)
class PasswordDigest:
    password_hash: str
    password_salt: str
    password_iterations: int


def login_enabled(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    mode = str(environ.get("HTC_LOGIN_MODE", "development")).strip().lower()
    return mode in {"active", "enabled", "enforced", "production", "on", "true", "1"}


def normalise_email(email: str) -> str:
    """The one spelling of an address. Everything that stores or looks up a user goes through it.

    A customer types their email the way they feel like typing it, and " Karuna@Duroflex.com " has
    to find the account created as "karuna@duroflex.com" or the sign-in is a lottery.
    """
    return str(email or "").strip().lower()


def password_complaint(password: str) -> str | None:
    """Why this password cannot be used, in words for the person choosing it, or None if it can."""
    value = str(password or "")
    if len(value) < MINIMUM_PASSWORD_LENGTH:
        return f"Use at least {MINIMUM_PASSWORD_LENGTH} characters."
    kinds = sum((
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(not c.isalnum() for c in value),
    ))
    if kinds < 3:
        return ("Use at least three of these four: a small letter, a capital, a digit, "
                "a punctuation mark.")
    return None


def password_rules() -> tuple[str, ...]:
    """What a password a customer chooses has to be, said before they choose it.

    Stefan, 27 November 2026, on the welcome email: *"make sure that the PW requirements are made
    clear (nr of chars etc.)"*. Until then the rules were said only as a refusal, after a
    password that broke one had been typed twice. `password_complaint` is the test; this is the
    same test in words, and the two are held together by a test of their own.
    """
    return (
        f"at least {MINIMUM_PASSWORD_LENGTH} characters",
        "at least three of these four: a small letter, a capital letter, a digit, "
        "a punctuation mark",
        "different from the start password",
    )


def is_platform_admin_email(email: str) -> bool:
    return hmac.compare_digest(str(email or "").strip().lower(), PLATFORM_ADMIN_EMAIL)


def hash_password(password: str, *, salt_hex: str | None = None, iterations: int = PBKDF2_ITERATIONS) -> PasswordDigest:
    if not password:
        raise ValueError("password cannot be empty")
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return PasswordDigest(digest.hex(), salt.hex(), iterations)


def verify_password(password: str, *, password_hash: str, password_salt: str, password_iterations: int) -> bool:
    if not password or not password_hash or not password_salt:
        return False
    candidate = hash_password(password, salt_hex=password_salt, iterations=password_iterations)
    return hmac.compare_digest(candidate.password_hash, password_hash)
