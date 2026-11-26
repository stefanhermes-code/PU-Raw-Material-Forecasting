"""The application sends an email, and keeps a record of every one it tried to send.

CR-2026-11-26-01, second piece of work (v1.2.135). Until this the delivery code had a place for an
email sender and nothing in it: every email delivery ended as "Email delivery transport is not
configured", and two of the three products on the deck - the Price Monitor and Forecast Alerts -
are things that arrive by email.

THE ROAD
--------
Microsoft Graph, as an application: `POST /users/{mailbox}/sendMail` with a token the application
gets for itself. Not SMTP with the mailbox's password. Microsoft switches password sign-in for SMTP
off by default at the end of December 2026 and has said it will remove it; a sender built on it
this month would have stopped next month.

THE ADDRESS
-----------
Stefan, 26 November 2026: the mail comes from s.hermes@htcglobal.asia - *"it is an alias in my
current MS account"*. So there are two addresses and they are not the same thing:

  the mailbox   the account the mail is sent THROUGH, and whose Sent Items keeps a copy;
  the sender    the address the customer SEES, which is an alias of that mailbox.

Whether Microsoft lets the alias stand as the sender depends on a setting of the organisation
("send from alias"). Where it is off, Graph either refuses the message or quietly sends it from
the mailbox's main address. The first is reported here in plain words; the second cannot be seen
from this side at all, which is why the test email says which address it was asked to come from
and asks the reader to look.

WHAT "ACCEPTED" MEANS
---------------------
Graph answers 202 when it has taken the message for sending. That is all this application can
know. It is recorded as ACCEPTED, never as delivered: a wrong address bounces later, into the
mailbox, where nothing here reads it.

Nothing in this module prints, logs or returns a secret. A page can say which NAMES are missing.
"""
from __future__ import annotations

import base64
import mimetypes
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import uuid4

# ------------------------------------------------------------------------------------ the settings
TENANT = "MAIL_TENANT_ID"
CLIENT = "MAIL_CLIENT_ID"
SECRET = "MAIL_CLIENT_SECRET"
MAILBOX = "MAIL_MAILBOX"
SENDER = "MAIL_FROM"
# The three that have no default: an app registration is somebody's to make, and nobody can guess it.
REQUIRED = (TENANT, CLIENT, SECRET)

# D7 of the change request. Both can be overridden by name without a release; neither is a secret.
# The mailbox is the owner's account, the one address this application already knows by name.
from raw_material_forecasting.platform_admin.auth import PLATFORM_ADMIN_EMAIL  # noqa: E402

DEFAULT_MAILBOX = PLATFORM_ADMIN_EMAIL
DEFAULT_SENDER = "s.hermes@htcglobal.asia"                         # an alias of that mailbox

# Graph takes a message with its attachments in one request of at most 4 MB, and an attachment
# travels as base64, a third larger than the file. Above this the request is refused by Microsoft
# with a size error that names nothing; below it there is room for the rest of the message.
ATTACHMENTS_LIMIT_BYTES = 2_900_000

ACCEPTED = "ACCEPTED"
REFUSED = "REFUSED"
NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class MailSettings:
    tenant_id: str
    client_id: str
    client_secret: str
    mailbox: str
    sender: str

    def __repr__(self) -> str:      # a settings object in a traceback must not be a secret in a log
        return f"MailSettings(mailbox={self.mailbox!r}, sender={self.sender!r})"


def missing_settings(env: Mapping[str, str] | None = None) -> list[str]:
    """The NAMES that have to be set before an email can go. Empty when it can."""
    env = os.environ if env is None else env
    return [name for name in REQUIRED if not str(env.get(name, "")).strip()]


def settings_from_environment(env: Mapping[str, str] | None = None) -> MailSettings | None:
    env = os.environ if env is None else env
    if missing_settings(env):
        return None
    return MailSettings(
        tenant_id=str(env[TENANT]).strip(), client_id=str(env[CLIENT]).strip(),
        client_secret=str(env[SECRET]).strip(),
        mailbox=str(env.get(MAILBOX, "")).strip() or DEFAULT_MAILBOX,
        sender=str(env.get(SENDER, "")).strip() or DEFAULT_SENDER)


def addresses(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """(mailbox, sender) as they would be used, whether or not the rest is set. Neither is secret."""
    env = os.environ if env is None else env
    return (str(env.get(MAILBOX, "")).strip() or DEFAULT_MAILBOX,
            str(env.get(SENDER, "")).strip() or DEFAULT_SENDER)


# ---------------------------------------------------------------------------------- the message
@dataclass(frozen=True)
class Attachment:
    name: str
    content: bytes
    content_type: str = "application/octet-stream"

    @classmethod
    def from_file(cls, path: Path | str) -> "Attachment":
        path = Path(path)
        guessed, _ = mimetypes.guess_type(path.name)
        return cls(path.name, path.read_bytes(), guessed or "application/octet-stream")


class MailRefused(RuntimeError):
    """The message did not go. The text is for a person: what was refused, and what to do."""


def _valid(address: str) -> bool:
    local, _, domain = str(address).strip().partition("@")
    return bool(local) and "." in domain and " " not in address.strip()


# What Microsoft says, and what it means for whoever is reading the page. Matched on the code, which
# is stable; the sentence that comes with it is not. An error that is not here is shown as it came.
_WHAT_IT_MEANS = (
    ("AADSTS7000215", "The client secret is wrong. Copy the secret's VALUE, not its ID, into "
                      f"{SECRET}."),
    ("AADSTS7000222", f"The client secret has expired. Make a new one and put it in {SECRET}."),
    ("AADSTS700016", f"Microsoft knows no application with that ID in that organisation. Check "
                     f"{CLIENT} and {TENANT}."),
    ("AADSTS90002", f"Microsoft knows no organisation with that ID. Check {TENANT}."),
    ("AADSTS900023", f"Microsoft knows no organisation with that ID. Check {TENANT}."),
    ("ErrorSendAsDenied", "Microsoft will not send from that address. Sending from an alias is "
                          "not switched on for the organisation, or the address is not an alias "
                          "of the mailbox."),
    ("ErrorAccessDenied", "The application is not allowed to send mail. It needs the Microsoft "
                          "Graph application permission Mail.Send, with admin consent, and any "
                          "access policy has to include the mailbox."),
    ("Authorization_RequestDenied", "The application is not allowed to send mail. It needs the "
                                    "Microsoft Graph application permission Mail.Send, with admin "
                                    "consent."),
    ("ErrorInvalidUser", f"Microsoft knows no mailbox with that address. Check {MAILBOX}."),
    ("ResourceNotFound", f"Microsoft knows no mailbox with that address. Check {MAILBOX}."),
    ("MailboxNotEnabledForRESTAPI", "That account has no Exchange Online mailbox to send from."),
    ("ErrorInvalidRecipients", "Microsoft refused a recipient's address."),
)


def _in_plain_words(status: int, code: str, message: str) -> str:
    haystack = f"{code} {message}"
    for known, meaning in _WHAT_IT_MEANS:
        if known in haystack:
            return f"{meaning} (Microsoft: {known})"
    said = " ".join(str(message).split())[:300]
    return f"Microsoft refused the message ({status} {code}): {said}".strip()


def _error_of(response: Any) -> tuple[str, str]:
    """(code, message) from either of the two shapes Microsoft answers a failure in."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - a failure with a body that is not JSON is still a failure
        return "", str(getattr(response, "text", ""))[:300]
    if not isinstance(body, dict):
        return "", str(body)[:300]
    error = body.get("error")
    if isinstance(error, dict):                                     # Graph
        return str(error.get("code", "")), str(error.get("message", ""))
    return str(error or ""), str(body.get("error_description", ""))      # the token endpoint


def _post(url: str, **kwargs: Any) -> Any:
    import requests

    return requests.post(url, timeout=30, **kwargs)


class GraphMailSender:
    """Sends through one mailbox, as one sender. Holds a token for as long as Microsoft says."""

    def __init__(self, settings: MailSettings, post: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.time):
        self.settings = settings
        self._post = post or _post
        self._clock = clock
        self._token: str | None = None
        self._token_until = 0.0
        self._lock = threading.Lock()

    def _bearer(self) -> str:
        with self._lock:
            if self._token and self._clock() < self._token_until:
                return self._token
            try:
                response = self._post(
                    f"https://login.microsoftonline.com/{self.settings.tenant_id}/oauth2/v2.0/token",
                    data={"grant_type": "client_credentials",
                          "client_id": self.settings.client_id,
                          "client_secret": self.settings.client_secret,
                          "scope": "https://graph.microsoft.com/.default"})
            except Exception as exc:  # noqa: BLE001 - no network is a refusal like any other
                raise MailRefused(f"Microsoft could not be reached to sign the application in "
                                  f"({type(exc).__name__}).") from None
            if response.status_code != 200:
                raise MailRefused(_in_plain_words(response.status_code, *_error_of(response)))
            body = response.json()
            self._token = str(body["access_token"])
            # A minute early, so a token is never used in its last seconds.
            self._token_until = self._clock() + max(0, int(body.get("expires_in", 3600)) - 60)
            return self._token

    def send(self, recipients: Sequence[str], *, subject: str, body: str,
             attachments: Sequence[Attachment] = ()) -> None:
        """Hand one message to Microsoft. Returns when it was taken; raises `MailRefused` if not."""
        to = [str(address).strip() for address in recipients if str(address).strip()]
        if not to:
            raise MailRefused("There is nobody to send it to.")
        wrong = [address for address in to if not _valid(address)]
        if wrong:
            raise MailRefused("Not an email address: " + ", ".join(wrong))
        size = sum(len(one.content) for one in attachments)
        if size > ATTACHMENTS_LIMIT_BYTES:
            raise MailRefused(
                f"The attachments are {size / 1_000_000:.1f} MB and one message takes "
                f"{ATTACHMENTS_LIMIT_BYTES / 1_000_000:.1f} MB.")
        message: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "from": {"emailAddress": {"address": self.settings.sender}},
            "toRecipients": [{"emailAddress": {"address": address}} for address in to],
        }
        if attachments:
            message["attachments"] = [{
                "@odata.type": "#microsoft.graph.fileAttachment", "name": one.name,
                "contentType": one.content_type,
                "contentBytes": base64.b64encode(one.content).decode("ascii")} for one in attachments]
        token = self._bearer()
        try:
            response = self._post(
                f"https://graph.microsoft.com/v1.0/users/{self.settings.mailbox}/sendMail",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": message, "saveToSentItems": True})
        except Exception as exc:  # noqa: BLE001
            raise MailRefused(f"Microsoft could not be reached to send the message "
                              f"({type(exc).__name__}).") from None
        if response.status_code == 401:
            self._token = None              # a token Microsoft no longer takes is not kept
        if response.status_code != 202:
            raise MailRefused(_in_plain_words(response.status_code, *_error_of(response)))


# ------------------------------------------------------------------------------------- the record
@dataclass(frozen=True)
class MailRecord:
    """One email the application tried to send: to whom, what, when, and whether it was taken."""
    mail_id: str
    company_id: str             # "" for mail that belongs to no customer - a test, say
    purpose: str                # "Test", "Scheduled delivery", later "Price Monitor", "Forecast Alert"
    sender: str
    recipients: tuple[str, ...]
    subject: str
    attachments: tuple[str, ...]
    status: str                 # ACCEPTED, REFUSED or NOT_CONFIGURED
    error: str
    sent_at: datetime
    sent_by: str


class Mailer:
    """The one way an email leaves the application. Every attempt is recorded, sent or not.

    `sender` is None where the application has not been given its settings. An attempt is then
    recorded as NOT_CONFIGURED with the names that are missing, so that "we never sent it" is in
    the record and not only in somebody's memory.
    """

    def __init__(self, record: Callable[[MailRecord], None], sender: GraphMailSender | None = None,
                 *, sender_address: str | None = None, missing: Iterable[str] = (),
                 now_fn: Callable[[], datetime] | None = None):
        self._record = record
        self.sender = sender
        self.sender_address = sender_address or (sender.settings.sender if sender else DEFAULT_SENDER)
        self.missing = tuple(missing)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    @property
    def configured(self) -> bool:
        return self.sender is not None

    def send(self, recipients: Sequence[str], *, subject: str, body: str, purpose: str,
             sent_by: str, company_id: str = "",
             attachments: Sequence[Attachment] = ()) -> MailRecord:
        status, error = ACCEPTED, ""
        if self.sender is None:
            status = NOT_CONFIGURED
            error = ("The application has not been given what it needs to send email"
                     + (": " + ", ".join(self.missing) if self.missing else "") + ".")
        else:
            try:
                self.sender.send(recipients, subject=subject, body=body, attachments=attachments)
            except MailRefused as exc:
                status, error = REFUSED, str(exc)
        made = MailRecord(
            mail_id=str(uuid4()), company_id=company_id, purpose=purpose,
            sender=self.sender_address,
            recipients=tuple(str(address).strip() for address in recipients if str(address).strip()),
            subject=subject, attachments=tuple(one.name for one in attachments), status=status,
            error=error[:1000], sent_at=self._now_fn(), sent_by=sent_by.strip())
        self._record(made)
        return made


def mailer_for(repo: Any, env: Mapping[str, str] | None = None,
               post: Callable[..., Any] | None = None) -> Mailer:
    """The mailer the application uses: recorded in `repo`, configured from the environment."""
    settings = settings_from_environment(env)
    _mailbox, sender_address = addresses(env)
    if settings is None:
        return Mailer(repo.add_mail, None, sender_address=sender_address,
                      missing=missing_settings(env))
    return Mailer(repo.add_mail, GraphMailSender(settings, post=post))


class DeliveryMail:
    """What `reporting_delivery.DeliveryExecutionService` asks an email adapter to be.

    A refusal is raised, because that service records a delivery as FAILED with the reason when
    its adapter raises; the email itself is in the mail record either way.
    """

    def __init__(self, mailer: Mailer, *, sent_by: str = "Delivery"):
        self.mailer = mailer
        self.sent_by = sent_by

    def send(self, artifact_path: Path, recipients: tuple[str, ...], *, subject: str,
             body: str) -> str:
        made = self.mailer.send(recipients, subject=subject, body=body,
                                purpose="Scheduled delivery", sent_by=self.sent_by,
                                attachments=[Attachment.from_file(artifact_path)])
        if made.status != ACCEPTED:
            raise MailRefused(made.error)
        return f"mail:{made.mail_id}"


def delivery_adapter(repo: Any, *, sent_by: str = "Delivery",
                     env: Mapping[str, str] | None = None) -> DeliveryMail | None:
    """The email adapter a delivery service is built with, or None while email is not set up.

    None, and not an adapter that fails, because the delivery service already knows what to do
    with None: it records the delivery as NOT_CONFIGURED, which is the truth. There are three
    places that build a delivery service - the Send page, the schedule on the admin page and the
    worker - and until v1.2.135 all three passed None by hand. They ask here now, so the day
    email is set up it is set up for all three.
    """
    mailer = mailer_for(repo, env)
    return DeliveryMail(mailer, sent_by=sent_by) if mailer.configured else None
