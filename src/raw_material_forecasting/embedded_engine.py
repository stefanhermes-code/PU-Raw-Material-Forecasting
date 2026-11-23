"""The market-data engine, running inside the page's own process.

WHY THIS EXISTS. On Stefan's machine Run-MVP.bat starts two programs: the engine on 7861 and the
Streamlit application on 7860. The container does the same, two processes behind one entrypoint.
Streamlit Community Cloud gives neither - it runs one command, `streamlit run app.py`, and there
is nowhere to put a second process. Without an engine every one of the page's engine calls fails,
`_safe_get` turns the failure into "engine unavailable", and every page function answers with an
empty list. The customer would get a working sign-in and blank markets: no error, nothing drawn.

So the engine runs here instead, on a thread, serving on loopback to this process alone. The page
is unchanged - it still speaks HTTP to a URL - and every call site, every timeout and every error
path stays exactly as it is on Stefan's machine. The only thing that moves is where the server
lives. A thread rather than a subprocess because Streamlit re-runs the script top to bottom on
every click: a subprocess would need its own lifetime, its own port file and its own orphan
cleanup, while a thread dies with the process that owns it.

ONE STORE, ONE WRITER - and this one does not write. `create_app` runs the whole housekeeping
before it returns unless told otherwise: history imports, reference packs, repairs, the
consolidated series. That work belongs to the machine that owns the data. Two engines doing it
against one Supabase project would fight over the same consolidated series, so this forces
ENGINE_HOUSEKEEPING=0 and FORECAST_WARM_UP=0 before the import rather than asking the host to
remember. A deployment must not be able to start housekeeping because somebody forgot a variable.

THE NAME COLLISION. The engine's package is called `app`, and the Streamlit entrypoint is a file
called `app.py` in the folder above it. Whichever of the two sys.path finds first wins, and a
`import app.main` that lands on app.py fails with "No module named 'app.main'; 'app' is not a
package". The bootstrap at the top of app.py puts market_data_engine FIRST for that reason, and
`engine_root` here is the same path, so a caller who skipped the bootstrap still gets it right.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

TRUTHY = {"1", "true", "yes", "on"}

# What a cold start is allowed to take. The import alone opens the store in Singapore and builds
# the source registry; on a cloud container from a standing start that has been seen to take the
# better part of a minute. Three minutes is the same ceiling the container's entrypoint allows.
START_TIMEOUT_SECONDS = 180.0

_STARTED: dict[str, str] = {}
_LOCK = threading.RLock()


class EngineDidNotStart(RuntimeError):
    """The in-process engine never began listening. The message says how far it got."""


def wanted() -> bool:
    """Whether this deployment wants the engine in its own process.

    Off unless asked for, so Stefan's machine is untouched: there the engine is already running on
    7861 and starting a second one would mean two engines on one store.
    """
    return os.environ.get("HTC_EMBEDDED_ENGINE", "").strip().lower() in TRUTHY


def engine_root() -> Path:
    """The folder that holds the engine's `app` package."""
    return Path(__file__).resolve().parents[2] / "market_data_engine"


def started_url() -> str | None:
    """The URL of the engine this process started, or None if it has not started one."""
    with _LOCK:
        return _STARTED.get("url")


def _free_port() -> int:
    """A port nobody is using, chosen by the operating system.

    Not 7861. On Stefan's machine that one is taken by the real engine, and a fixed port would
    make running the customer view beside Run-MVP.bat a port clash rather than a second opinion.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def start(*, timeout_seconds: float = START_TIMEOUT_SECONDS) -> str:
    """Start the engine on a thread and return the URL it answers on.

    Idempotent: Streamlit re-runs the script on every interaction, and the second call returns the
    first engine rather than starting another.
    """
    with _LOCK:
        running = _STARTED.get("url")
        if running:
            return running

        import uvicorn

        root = engine_root()
        if not (root / "app" / "main.py").is_file():
            raise EngineDidNotStart(f"no engine beside the application; looked in {root}")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        # Set BEFORE the import: `create_app` reads both at module import time, and by the time
        # the import returns the housekeeping has already run.
        os.environ["ENGINE_HOUSEKEEPING"] = "0"
        os.environ["FORECAST_WARM_UP"] = "0"

        from app.main import app as engine_app          # noqa: E402  (path set just above)

        port = _free_port()
        config = uvicorn.Config(
            engine_app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)

        failure: dict[str, BaseException] = {}

        def _serve() -> None:
            try:
                server.run()
            except BaseException as exc:                 # pragma: no cover - reported, not raised
                failure["error"] = exc

        thread = threading.Thread(target=_serve, name="market-data-engine", daemon=True)
        thread.start()

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if getattr(server, "started", False):
                url = f"http://127.0.0.1:{port}"
                _STARTED["url"] = url
                return url
            if not thread.is_alive():
                raise EngineDidNotStart(
                    f"the engine stopped while starting: {failure.get('error', 'no reason given')}")
            time.sleep(0.1)

        raise EngineDidNotStart(
            f"the engine did not answer within {timeout_seconds:.0f} seconds")
