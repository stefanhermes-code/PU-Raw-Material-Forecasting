"""The email that hands a user their sign-in.

Stefan, 27 November 2026, having set his own first password under Sheela Foam: *"Will the user get
an email with this PW?"* - they did not; the screen said "send it to the user" and left the
sending to him - and, offered a button that does it: *"Yes I think that welcome button saves me
time"*.

What the email holds is what a user needs on the first morning and nothing else: where to sign
in, the address they sign in with, the start password, and that the application will ask them to
replace it. The start password travels in this message; that is why the application makes them
choose their own before it opens (`set_user_password`, `must_change`).

Only the words are here. The sending is `mail.Mailer`, which records every attempt - to whom,
when, whether Microsoft took it - and never the body, so the password is in the message and in
no record of ours.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Mapping

from .auth import PLATFORM_ADMIN_EMAIL, password_rules

# Where a customer signs in. Not a secret; it can be set by name without a release, the same way
# the mailbox and the sender can.
ADDRESS = "APPLICATION_URL"
DEFAULT_ADDRESS = "https://pu-raw-material-forecasting-application.streamlit.app/"
PURPOSE = "Welcome"
SUBJECT = "Your access to PU Raw Material Forecasting"


def application_address(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return str(env.get(ADDRESS, "")).strip() or DEFAULT_ADDRESS


def body(*, name: str, email: str, password: str, address: str,
         starts: date | None = None) -> str:
    """The message. `starts` is the first day of access where it has not begun yet."""
    greeting = f"Dear {name.strip()}," if str(name or "").strip() else "Hello,"
    opening = ("Your access to PU Raw Material Forecasting is ready." if starts is None else
               f"Your access to PU Raw Material Forecasting starts on {starts.day} "
               f"{starts:%B %Y}. You can sign in from that day.")
    return "\n".join([
        greeting,
        "",
        opening,
        "",
        f"Sign in at:      {address}",
        f"Email:           {email}",
        f"Start password:  {password}",
        "",
        "The first time you sign in, the application asks you to choose your own password. "
        "The start password stops working at that moment.",
        "",
        "Your own password has to be:",
        *[f"  - {rule}" for rule in password_rules()],
        "",
        "Your sign-in is personal. Please do not share it.",
        "",
        f"If anything does not work, reply to this email or write to {PLATFORM_ADMIN_EMAIL}.",
        "",
        "Kind regards,",
        "Stefan Hermes",
        "HTC Global",
    ])
