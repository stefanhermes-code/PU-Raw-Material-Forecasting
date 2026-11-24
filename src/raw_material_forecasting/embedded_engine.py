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

A PUBLISH REPLACES THE PAGE AND NOT THE ENGINE - so the page replaces the engine itself.
Demonstrated on the live deployment on 24 November 2026: after two publishes the pages ran
v1.2.114 and this thread was still serving v1.2.112. The host pulls the new files and Streamlit
re-imports the page's modules; a thread that is already running keeps the functions it started
with. Until v1.2.115 the cure was the owner's click on "Reboot app", for every release that
touched the engine.

`start(release=...)` is now asked on every run which release the page is. Where the engine
answering was started for another one, a fresh engine is built from the files on disk and started
on a new port, and only once it answers is the old one retired. Three things make that safe to
leave switched on:

  * THE OLD ENGINE KEEPS ANSWERING UNTIL THE NEW ONE DOES, and if the new one does not start the
    old one stays, its modules are put back, and the release is not tried again for three
    minutes. The worst case is the state before this existed. Nothing here restarts the process
    or touches the host - a forced host restart is what took the deployment down on 23 November.
  * THE STATE LIVES ON `sys`, not in this module. Streamlit drops this module from `sys.modules`
    when a file changes and imports it again, so a module-level dictionary forgets the engine it
    started - which is exactly the moment it has to remember it.
  * THE RETIRED ENGINE GIVES ITS CONNECTIONS BACK. Its server is told to exit and its pool is
    disposed; the store's pooler counts every one (see `platform_store`).
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

# How long a release that would not start is left alone before it is tried again. The old engine
# is answering in the meantime, so this costs nothing but the wait.
RETRY_SECONDS = 180.0

# How long the engine being replaced goes on answering after the new one is up. A run that read
# the old address a moment before the swap finishes against the old engine instead of finding the
# port closed.
GRACE_SECONDS = 30.0

# On `sys`, so it outlives this module being imported again. `_STARTED` keeps its name: the page
# and the tests have always known it by that.
_STARTED: dict = sys.__dict__.setdefault("_htc_embedded_engine", {})
_LOCK = sys.__dict__.setdefault("_htc_embedded_engine_lock", threading.RLock())


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


def answering(release: str | None) -> bool:
    """Whether `start(release=release)` would return at once, starting nothing.

    True when an engine is up and it was started for this release - or this release was tried,
    would not start, and is being left alone for now. The page asks so that it only shows
    "Starting the market-data engine ..." when something is about to start.
    """
    with _LOCK:
        if not _STARTED.get("url"):
            return False
        return release is None or _STARTED.get("asked") == release or _left_alone(release)


def status() -> dict:
    """What is answering, for the one line the owner reads. No address, nothing secret."""
    with _LOCK:
        refused = _STARTED.get("refused") or {}
        return {"running": bool(_STARTED.get("url")), "asked": _STARTED.get("asked"),
                "reported": _STARTED.get("reported"), "replaced": _STARTED.get("replaced"),
                "refused": {name: why for name, (why, _when) in refused.items()}}


def _left_alone(release: str | None) -> bool:
    refused = (_STARTED.get("refused") or {}).get(release)
    return bool(refused) and (time.monotonic() - refused[1]) < RETRY_SECONDS


def _free_port() -> int:
    """A port nobody is using, chosen by the operating system.

    Not 7861. On Stefan's machine that one is taken by the real engine, and a fixed port would
    make running the customer view beside Run-MVP.bat a port clash rather than a second opinion.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _listening(port: int) -> bool:
    """Whether something on this machine accepts a connection on `port` right now."""
    with socket.socket() as probe:
        probe.settimeout(1.0)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _release_on_disk(root: Path) -> str:
    """The release the files beside the engine are, read the way the engine reads it."""
    try:
        return (root.parent / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _engine_modules(root: Path) -> dict:
    """The engine's own modules as Python holds them now: `app` and everything under it.

    By file as well as by name. The page is a file called app.py, and a module named `app` that
    does not live under the engine's folder is not the engine's to forget.
    """
    inside = str(root.resolve()).lower()
    found = {}
    for name, module in list(sys.modules.items()):
        if name != "app" and not name.startswith("app."):
            continue
        where = str(getattr(module, "__file__", "") or "")
        paths = [str(one) for one in getattr(module, "__path__", ()) or ()]
        if any(str(Path(one).resolve()).lower().startswith(inside) for one in [where, *paths] if one):
            found[name] = module
    return found


def _forget(root: Path) -> dict:
    """Take the engine's modules out of `sys.modules` and hand them back, so they can be restored."""
    held = _engine_modules(root)
    for name in held:
        sys.modules.pop(name, None)
    return held


def _serve(root: Path, timeout_seconds: float) -> dict:
    """Import the engine as the files on disk have it, start it, and wait until it answers."""
    import uvicorn

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # Set BEFORE the import: `create_app` reads both at module import time, and by the time
    # the import returns the housekeeping has already run.
    os.environ["ENGINE_HOUSEKEEPING"] = "0"
    os.environ["FORECAST_WARM_UP"] = "0"

    import app.main as engine_main                   # noqa: E402  (path set just above)

    # An engine module Python was already holding is the release it was imported as, not the one
    # on disk. Where the two differ the held one is stale: forget it and import what is there now.
    on_disk = _release_on_disk(root)
    if on_disk and str(getattr(engine_main, "VERSION", "")) != on_disk:
        _forget(root)
        import app.main as engine_main               # noqa: E402,F811

    engine_app = engine_main.app
    port = _free_port()
    config = uvicorn.Config(engine_app, host="127.0.0.1", port=port, log_level="warning",
                            access_log=False)
    server = uvicorn.Server(config)
    failure: dict[str, BaseException] = {}

    def _run() -> None:
        try:
            server.run()
        except BaseException as exc:                 # pragma: no cover - reported, not raised
            failure["error"] = exc

    thread = threading.Thread(target=_run, name="market-data-engine", daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return {"url": f"http://127.0.0.1:{port}", "server": server, "thread": thread,
                    "app": engine_app, "reported": str(getattr(engine_main, "VERSION", "") or "")}
        if not thread.is_alive():
            raise EngineDidNotStart(
                f"the engine stopped while starting: {failure.get('error', 'no reason given')}")
        time.sleep(0.1)
    server.should_exit = True
    raise EngineDidNotStart(f"the engine did not answer within {timeout_seconds:.0f} seconds")


def _retire(server, thread, engine_app) -> None:
    """Stop one engine and give its connections back. Never raises: it is tidying up."""
    try:
        server.should_exit = True
        if thread is not None:
            thread.join(timeout=20)
    except Exception:                                # pragma: no cover
        pass
    try:
        engine_app.state.session_factory.kw["bind"].dispose()
    except Exception:
        pass


def _retire_later(server, thread, engine_app, after_seconds: float) -> None:
    if after_seconds <= 0:
        _retire(server, thread, engine_app)
        return
    timer = threading.Timer(after_seconds, _retire, args=(server, thread, engine_app))
    timer.daemon = True
    timer.start()


def _strays(keep) -> list:
    """Engines this process is serving that nobody holds any more.

    One was left by every release before v1.2.115: its server was remembered in a module-level
    dictionary, and Streamlit importing the module again forgot it. Recognised by what it serves -
    an application carrying the engine's own service on its state - so no other server in this
    process, the page's own included, can be mistaken for one.
    """
    import gc

    found = []
    for candidate in gc.get_objects():
        try:
            if candidate is keep or type(candidate).__name__ != "Server":
                continue
            if not type(candidate).__module__.startswith("uvicorn"):
                continue
            served = getattr(getattr(candidate, "config", None), "app", None)
            # Started, and not already told to stop: uvicorn leaves `started` true on a server
            # that has shut down, so without the second test every engine ever retired would be
            # found again - and could be taken back as if it were answering.
            if (getattr(candidate, "started", False)
                    and not getattr(candidate, "should_exit", False)
                    and hasattr(getattr(served, "state", None), "market_data_service")):
                found.append((candidate, served))
        except Exception:                            # an object mid-collection; not ours to judge
            continue
    return found


def _say(text: str) -> None:
    """One line in the host's log. Releases and nothing else: no address, no store."""
    print(f"[market-data engine] {text}", flush=True)


def start(*, timeout_seconds: float = START_TIMEOUT_SECONDS, release: str | None = None,
          grace_seconds: float = GRACE_SECONDS) -> str:
    """Start the engine on a thread and return the URL it answers on.

    Idempotent: Streamlit re-runs the script on every interaction, and a call for the release that
    is already answering returns that engine rather than starting another.

    `release` is the release the PAGE is. Where the engine answering was started for a different
    one it is replaced - see the module's last section. A caller that names no release gets
    whatever is running, as it always did.
    """
    with _LOCK:
        running = _STARTED.get("url")
        if running and answering(release):
            return running

        root = engine_root()
        if not (root / "app" / "main.py").is_file():
            raise EngineDidNotStart(f"no engine beside the application; looked in {root}")

        if not running:
            try:
                fresh = _serve(root, timeout_seconds)
            except Exception as exc:
                # Nothing on record, but there may still be an engine answering: the one a
                # release before v1.2.115 started and forgot. On the way into this release that
                # is the only engine there is, so it is taken back rather than left running
                # beside an error page, and the new release is tried again later like any other.
                left = [(server, served) for server, served in _strays(None)
                        if _listening(int(server.config.port))]
                if not left:
                    raise
                server, served = left[0]
                why = f"{type(exc).__name__}: {str(exc)[:240]}"
                _STARTED.update(url=f"http://127.0.0.1:{int(server.config.port)}", server=server,
                                thread=None, app=served, reported="", asked=None)
                _STARTED.setdefault("refused", {})[release] = (why, time.monotonic())
                _say(f"v{release} would not start and the engine already running is kept: {why}")
                return _STARTED["url"]
            _STARTED.update(fresh, asked=release)
            _STARTED.pop("refused", None)
            for server, served in _strays(fresh["server"]):
                _say("retiring an engine left behind by an earlier release")
                _retire_later(server, None, served, grace_seconds)
            _say(f"v{fresh['reported'] or '?'} answering")
            return fresh["url"]

        # An engine is answering, and it was started for another release. Build the new one beside
        # it; the old one is retired only once the new one answers.
        before = {name: _STARTED.get(name) for name in ("server", "thread", "app", "reported")}
        held = _forget(root)
        try:
            fresh = _serve(root, timeout_seconds)
        except BaseException as exc:
            # Put back exactly what was there. The old engine imports some of its modules only
            # when a request needs them, and it must find its own release, not half of the next.
            _forget(root)
            sys.modules.update(held)
            _STARTED.setdefault("refused", {})[release] = (
                f"{type(exc).__name__}: {str(exc)[:240]}", time.monotonic())
            _say(f"v{release} would not start and v{before['reported'] or '?'} is still "
                 f"answering: {type(exc).__name__}: {str(exc)[:240]}")
            if not isinstance(exc, Exception):
                raise
            return running
        _STARTED.update(fresh, asked=release, replaced=before["reported"])
        _STARTED.pop("refused", None)
        _say(f"v{fresh['reported'] or '?'} answering; it replaced v{before['reported'] or '?'}")
        if before["server"] is not None:
            _retire_later(before["server"], before["thread"], before["app"], grace_seconds)
        return fresh["url"]
