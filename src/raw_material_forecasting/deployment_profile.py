"""A deployment that customers can reach carries its own switches, rather than trusting its host.

THE RULE, unchanged since the Dockerfile: a deployment must not be able to be reachable and open
at the same time because somebody forgot an environment variable. The container settles this with
ENV instructions baked into the image. Streamlit Community Cloud has no image and no environment
variables at all - everything a deployment there is given is typed by hand into a Secrets box - so
a forgotten line in that box would be a customer deployment with no sign-in, every market visible,
and nothing on the screen to say so.

So the published repository carries a marker file, and the marker forces the switches. Create the
file and the deployment is closed; there is no spelling of the Secrets box that opens it, because
these are set AFTER the secrets are read and they overwrite rather than default.

What is NOT here: DATABASE_URL. That one is a secret and belongs in the Secrets box. Everything
here is a policy decision about how the deployment behaves, and policy belongs with the code.

On Stefan's machine the marker does not exist, nothing is applied, and Run-MVP.bat opens the same
development screen it always did.
"""
from __future__ import annotations

import os
from pathlib import Path

MARKER_NAME = "HOSTED_DEPLOYMENT"

# Forced, not defaulted. Each line, and why it is not negotiable from the host:
#
#   HTC_LOGIN_MODE            active    - there is a sign-in screen at all
#   HTC_PLATFORM_ACCESS_MODE  enforced  - the entitlement decides what is drawn
#   HTC_TRUSTED_RUNTIME_USER  0         - nobody is whoever an environment variable says they are
#   HTC_EMBEDDED_ENGINE       1         - the engine runs in this process; there is no second one
#   ENGINE_HOUSEKEEPING       0         - this deployment reads; the owner's machine writes
#   FORECAST_WARM_UP          0         - same reason
HOSTED_SWITCHES: dict[str, str] = {
    "HTC_LOGIN_MODE": "active",
    "HTC_PLATFORM_ACCESS_MODE": "enforced",
    "HTC_TRUSTED_RUNTIME_USER": "0",
    "HTC_EMBEDDED_ENGINE": "1",
    "ENGINE_HOUSEKEEPING": "0",
    "FORECAST_WARM_UP": "0",
}


# WHAT A CUSTOMER PAGE SHOWS FOLLOWS STEFAN'S MACHINE (v1.2.128, 25 November 2026).
# Which markets a customer page shows is Stefan's choice, made on his machine and kept in a file
# there (`customer_controls`). The deployment had no such file, so it showed every market,
# including the 34 he had switched off. Stefan: "the live site should follow my machine." So the
# publish script carries the file with each release, under this name beside the marker, and the
# deployment is told to read it from there and nowhere else. No file, no choices: every market
# shows, which is what a deployment did before.
CONTROLS_NAME = "customer_page_controls.json"
CONTROLS_SWITCH = "PUFORECAST_CONTROLS"


def controls_path(root: Path) -> Path:
    return Path(root) / CONTROLS_NAME


def marker_path(root: Path) -> Path:
    return Path(root) / MARKER_NAME


def is_hosted(root: Path) -> bool:
    """Whether this checkout is a published customer deployment."""
    return marker_path(root).is_file()


def apply(root: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Put the hosted switches into the environment. Returns what was applied, or {}.

    Overwrites whatever is there. A host that set HTC_PLATFORM_ACCESS_MODE=open on a published
    deployment is a host that has made a mistake, and the mistake is one a customer would pay for.
    """
    target = os.environ if environ is None else environ
    if not is_hosted(root):
        return {}
    applied = {**HOSTED_SWITCHES, CONTROLS_SWITCH: str(controls_path(root))}
    target.update(applied)
    return applied
