"""The Price Monitor: which edition a customer is owed, what it is called, and what became of it.

CR-2026-11-26-01, fourth piece of work (v1.2.136). The deck sells "a written report on a fixed
schedule, quarterly or monthly". Stefan, 26 November 2026, on what that report is: *"you can use
the forecast report as per the button on the forecast page"*. So the Monitor is that report, for
every market the customer's product covers, in one document (`page_reports.monitor_docx`).

This module is the part with no screen and no store in it: the calendar, the names and the words
of the email. An edition is named for the period it is issued in - "December 2026", "Q4 2026" -
because that is what a customer calls it and what the price list sells: twelve a year, or four.

WHAT IS NOT HERE
----------------
Nothing in the application sends an edition by itself. A person makes it, looks at it and sends
it, from Platform Admin. Sending on a fixed day without anybody at the screen needs a machine
that is always on, which is the sixth piece of the change request and not this one. The page
says what is due and since when, so that "by hand" does not become "forgotten".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from raw_material_forecasting.platform_admin import products
from raw_material_forecasting.platform_admin.models import Subscription

MADE = "MADE"            # built and kept; nobody outside HTC has seen it
SENT = "SENT"            # Microsoft took the email it was attached to
HELD = "HELD"            # looked at and deliberately not sent
REPLACED = "REPLACED"    # made again; the later one is the edition

STATE_WORDS = {
    None: "Not made yet",
    MADE: "Made, not sent",
    SENT: "Sent",
    HELD: "Held back",
    REPLACED: "Replaced by a later one",
}

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class MonitorIssue:
    """One edition made for one customer's Price Monitor. The document itself is kept beside it."""
    issue_id: str
    subscription_id: str
    company_id: str
    edition: str
    title: str
    file_name: str
    markets: int
    size_bytes: int
    status: str
    made_at: datetime
    made_by: str
    sent_at: datetime | None = None
    sent_by: str = ""
    recipients: tuple[str, ...] = ()
    mail_id: str = ""
    note: str = ""


def _quarter(day: date) -> int:
    return (day.month - 1) // 3 + 1


def edition_of(subscription: Subscription, today: date) -> str:
    """The edition this customer is owed today: the month's, or the quarter's."""
    if subscription.edition == products.QUARTERLY:
        return f"Q{_quarter(today)} {today.year}"
    return today.strftime("%B %Y")


def due_since(subscription: Subscription, today: date) -> date:
    """The day this edition became owed: the period's first day, or the product's if that is later."""
    if subscription.edition == products.QUARTERLY:
        opened = date(today.year, 3 * (_quarter(today) - 1) + 1, 1)
    else:
        opened = today.replace(day=1)
    return max(opened, subscription.effective_from.date())


def current_issue(issues: Iterable[MonitorIssue], subscription_id: str,
                  edition: str) -> MonitorIssue | None:
    """The issue that IS this edition: the newest one made for it that was not made again."""
    mine = [one for one in issues if one.subscription_id == subscription_id
            and one.edition == edition and one.status != REPLACED]
    return max(mine, key=lambda one: one.made_at) if mine else None


def why_left_out(forecast, today: date) -> str | None:
    """Why a market's forecast cannot go in an edition, or None when it can.

    An edition is "what the forecast says about the period ahead" (the deck). On the first real
    edition, 26 November 2026, three of 46 markets carried something else: one a forecast for
    September, October and November; one for "September, January, November", in that order; one
    the same date twice. Each is what the Forecast page shows for that market today, and each is a
    fault in that market's forecast, not in the report. A customer is not sent them: the market
    is left out, and the edition's own record names it and says why, so the fault is seen by
    somebody who can mend it rather than by the customer.

    `forecast` is the report's rows: (date, value, low, high).
    """
    days = []
    for row in forecast or ():
        try:
            days.append(date.fromisoformat(str(row[0])[:10]))
        except ValueError:
            return "a forecast date cannot be read"
    if not days:
        return "it carries no forecast"
    if any(later <= earlier for earlier, later in zip(days, days[1:])):
        return "its forecast dates are not in order"
    if days[0] < today.replace(day=1):
        return f"its forecast starts in {days[0]:%B %Y}, which is behind us"
    return None


def _words(subscription: Subscription) -> tuple[str, str]:
    """(group, regions) as a customer reads them."""
    group = products.group(subscription.material_group).name
    regions = ", ".join(region.name for region in products.REGIONS
                        if region.code in subscription.regions)
    return group, regions


def subtitle(subscription: Subscription) -> str:
    return " · ".join(_words(subscription))


def _for_a_file_name(text: str) -> str:
    return "-".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def file_name(company_name: str, subscription: Subscription, edition: str) -> str:
    group, _regions = _words(subscription)
    return "Price_Monitor_" + "_".join(
        _for_a_file_name(part) for part in (company_name, group, edition)) + ".docx"


def email_subject(subscription: Subscription, edition: str) -> str:
    group, _regions = _words(subscription)
    return f"HTC Global Price Monitor - {group} - {edition}"


def email_body(subscription: Subscription, edition: str, markets: int) -> str:
    """What the customer reads above the attachment. Shown on the page before it is sent."""
    group, regions = _words(subscription)
    return (
        "Dear customer,\n\n"
        f"Attached is the HTC Global Price Monitor for {group}, {regions}: the {edition} edition.\n\n"
        f"It covers {markets} market{'' if markets == 1 else 's'}. For each one it shows the "
        "price over the last six months, the last three months in figures, and the forecast "
        "for the months ahead.\n\n"
        "Kind regards,\n"
        "HTC Global")
