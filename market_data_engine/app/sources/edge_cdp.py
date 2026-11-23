"""
Rendered-page acquisition through a real Microsoft Edge process attached over
the Chromium DevTools Protocol.

Why this exists (verified 2026-09-08 on Stefan's PC, see echemi_poc/FINDINGS.md):
  * ECHEMI sits behind Aliyun WAF. Its silent JavaScript check passes for a
    normal Edge process and fails for anything that looks automated:
    Playwright/Selenium-launched browsers (automation switches) and *any*
    `--headless` Edge are sent to the "slide to verify" page.
  * A real msedge.exe started with a dedicated profile, no automation
    switches and its window placed off-screen passes the check every time,
    from an empty profile, with no human interaction.
  * Edge >= 136 refuses --remote-debugging-port on the default profile, so a
    dedicated --user-data-dir is mandatory.

Nothing here bypasses or reverse-engineers the WAF: we launch the vendor's
browser the normal way and only *read* what it rendered.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path


class EdgeUnavailable(RuntimeError):
    """Edge or the CDP client is not available on this machine."""


class RenderedPageChallenge(RuntimeError):
    """The site answered with an access-verification page instead of content."""


CHALLENGE_MARKERS = ("Access Verification", "slide to complete the verification", "Verification failed")

# What the WAF sends before any of the above is on screen. The slider text only appears once the
# page's JavaScript has run and failed; a plain HTTP client never runs it, so it receives HTTP 200
# and 110 KB that contain none of the words above and none of the data either - the same 110,527
# bytes for five different URLs (Stefan's machine, 18 September 2026). Taking that for a price table
# is the failure mode worth guarding against.
WAF_MARKERS = ("aliyun_waf", "_waf_", "renderData")


def is_challenge(markup: str) -> bool:
    """Whether this is the WAF answering rather than the site."""
    text = str(markup or "")
    return any(m in text for m in CHALLENGE_MARKERS) or any(m in text for m in WAF_MARKERS)

# One budget, and every clock inside it. The collector gives a source 90 seconds. Navigation used to
# carry its own 60 on top of the read's 40 and the launch's 30, so a page that never settled ran past
# the collector, which then abandoned the thread with Edge still on screen (Stefan, 18 September 2026).
# Worst case now: 25 launch + 20 navigation + 30 read = 75, inside the join deadline and inside the 90.
GOTO_TIMEOUT_SECONDS = 20.0

# How long a host that has answered with the verification page is left alone. A challenge is the
# site's decision, not a transient fault, and repeating it inside one afternoon costs the collection
# cycle a minute and a half per source and puts a browser window on the user's screen each time.
# The source still reports as failing; it just stops being retried until this has passed.
CHALLENGE_BACKOFF_SECONDS = 6 * 3600
_CHALLENGED: dict[str, float] = {}


def _host_of(url: str) -> str:
    text = str(url or "")
    without_scheme = text.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0].lower()


def challenged_until(url: str, now: float | None = None) -> float | None:
    """When this host's back-off ends, or None if it is not in back-off."""
    now = time.time() if now is None else now
    until = _CHALLENGED.get(_host_of(url))
    if until is None or until <= now:
        return None
    return until


def record_challenge(url: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _CHALLENGED[_host_of(url)] = now + CHALLENGE_BACKOFF_SECONDS


def clear_challenges() -> None:
    _CHALLENGED.clear()


SESSION_ROOT_NAME = "edge-cdp-sessions"
LEGACY_PROFILE_NAME = "edge-cdp-profile"

# A fetch reads one page and times out at about a minute. Anything under the session root older than
# this belongs to a run that is over, whatever its browser is still doing.
STALE_AFTER_SECONDS = 900


def profile_root() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / "HTCGlobal" / "PUForecast"


def default_profile_dir() -> Path:
    """The old shared profile. Kept so an explicit caller still resolves; no launch uses it now."""
    base = os.environ.get("PUFORECAST_EDGE_PROFILE")
    if base:
        return Path(base)
    return profile_root() / LEGACY_PROFILE_NAME


def new_session_profile_dir() -> Path:
    """The profile a launch uses: one directory, kept between fetches.

    0.4.179 gave every launch an empty directory of its own. That threw away Aliyun's pass tokens -
    `acw_tc`, `cdn_sec_tc` - which this profile had been holding and refreshing since 8 September,
    so every fetch arrived cold and ECHEMI answered with the verification slider (Stefan,
    18 September 2026). The profile's history is the asset; a fetch has to inherit it.

    A stale browser can no longer be handed the next request, which is what the separate directory
    was for: `_PROFILE_LOCK` admits one fetch at a time and each launch terminates whatever is
    holding the profile before it starts.
    """
    return default_profile_dir()


# One fetch at a time. Every fetch now shares one profile, and Edge will not start a second browser
# for a profile that already has one - it hands its work to the running browser and exits. Serialising
# is what makes "terminate whatever holds this profile, then launch" safe to do on every fetch.
_PROFILE_LOCK = threading.RLock()


def _is_windows() -> bool:
    return os.name == "nt"


def _powershell(script: str) -> None:
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25, check=False,
        )
    except Exception:
        pass


def kill_edge_for_profile(profile_dir: Path) -> None:
    """Terminate every Edge process holding this profile directory.

    The process launched here is a stub: on Windows it hands off to a browser process and exits at
    once, so waiting on it and killing it on timeout - which is what this used to do - reliably
    killed nothing. The profile directory is what identifies the browser that was actually started.
    """
    token = str(profile_dir)
    if not token:
        return
    if _is_windows():
        quoted = token.replace("'", "''")
        _powershell(
            "$t = [regex]::Escape('" + quoted + "'); "
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" -ErrorAction SilentlyContinue "
            "| Where-Object { $_.CommandLine -match $t } "
            "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        return
    try:
        subprocess.run(["pkill", "-f", token], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15, check=False)
    except Exception:
        pass


def sweep_stale_sessions(now: float | None = None) -> int:
    """Clear the per-launch profile directories 0.4.179 created, and the browsers holding them.

    Nothing launches into those any more, so anything still holding one is a browser that was left
    behind. The working profile is not touched here: `EdgeCdpSession` clears that one under the
    lock, at the moment it launches.
    """
    now = time.time() if now is None else now
    root = profile_root() / SESSION_ROOT_NAME
    swept = 0
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            try:
                if now - child.stat().st_mtime < STALE_AFTER_SECONDS:
                    continue
            except OSError:
                continue
            kill_edge_for_profile(child)
            shutil.rmtree(child, ignore_errors=True)
            swept += 1
    return swept


def find_edge() -> str | None:
    override = os.environ.get("PUFORECAST_EDGE_EXE")
    if override and Path(override).is_file():
        return override
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if not root:
            continue
        candidate = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def launch_args(exe: str, port: int, profile_dir: Path) -> list[str]:
    """Command line for a *normal* Edge: no automation switches, never headless."""
    return [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=msEdgeStartupBoost",
        "--window-size=1440,1100",
        "--window-position=-32000,-32000",  # windowed but off-screen: headless Edge is challenged by the WAF
        "about:blank",
    ]


class EdgeCdpSession:
    """One off-screen Edge process, attached over CDP for the lifetime of the `with` block."""

    def __init__(self, *, profile_dir: Path | None = None, launch_timeout: float = 25.0):
        self.profile_dir = Path(profile_dir) if profile_dir is not None else new_session_profile_dir()
        self._holding_lock = False
        self.launch_timeout = launch_timeout
        self.port: int | None = None
        self._proc: subprocess.Popen | None = None
        self._pw = None
        self._browser = None
        self._context = None

    # -- lifecycle -------------------------------------------------------
    def __enter__(self) -> "EdgeCdpSession":
        exe = find_edge()
        if not exe:
            raise EdgeUnavailable("msedge.exe not found")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise EdgeUnavailable("playwright is not installed") from exc

        _PROFILE_LOCK.acquire()
        self._holding_lock = True
        sweep_stale_sessions()
        # Whatever is holding this profile is from a fetch that is over: the lock says no fetch of
        # ours is running. Clearing it is what stops the next launch being handed off to it.
        kill_edge_for_profile(self.profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        args = launch_args(exe, self.port, self.profile_dir)
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + self.launch_timeout
        while not _port_open(self.port):
            if time.time() > deadline or self._proc.poll() is not None:
                # A launch that never opened its port still leaves a browser behind when the stub
                # handed off before exiting. Tear down by profile, not by the stub's pid.
                self._await_exit(timeout=2.0)
                kill_edge_for_profile(self.profile_dir)
                self._release()
                raise EdgeUnavailable(
                    f"Edge did not open DevTools port {self.port} within {self.launch_timeout}s "
                    f"(profile {self.profile_dir})"
                )
            time.sleep(0.25)
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
            self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        except BaseException:
            # Attaching failed with a browser already running and the lock already held. Without
            # this the browser stays up and no later fetch can take the lock again.
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Ask Edge to close, give it a moment, then make sure it is gone.

        The clean path is tried first so a browser that can shut itself down does. What follows is
        not conditional on it: the browser holding this profile is terminated whether or not the
        stub we launched has already exited, and the profile directory goes with it.
        """
        try:
            if self._browser is not None:
                try:
                    self._browser.new_browser_cdp_session().send("Browser.close")
                except Exception:
                    pass
                try:
                    self._browser.close()
                except Exception:
                    pass
        finally:
            self._browser = None
            self._context = None
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None
            self._await_exit()
            kill_edge_for_profile(self.profile_dir)
            self._release()

    def _await_exit(self, timeout: float = 5.0) -> None:
        """Give a clean shutdown its chance: wait for the stub, then for the port to go quiet."""
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.wait(timeout=timeout)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self.port is None:
            return
        deadline = time.time() + timeout
        while _port_open(self.port) and time.time() < deadline:
            time.sleep(0.25)

    def _release(self) -> None:
        if self._holding_lock:
            self._holding_lock = False
            try:
                _PROFILE_LOCK.release()
            except RuntimeError:
                pass

    # -- reading ---------------------------------------------------------
    def rendered_text(self, url: str, *, content_markers: tuple[str, ...] = (), any_markers: tuple[str, ...] = (),
                      timeout: float = 30.0) -> str:
        """Navigate and return document.body.innerText once all `content_markers`
        and at least one of `any_markers` (when given) are present."""
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_SECONDS * 1000)
            deadline = time.time() + timeout
            text = ""
            while True:
                try:
                    text = page.evaluate("document.body ? document.body.innerText : ''") or ""
                except Exception:
                    text = ""
                if text and all(m in text for m in content_markers) and (not any_markers or any(m in text for m in any_markers)):
                    return text
                if any(marker in text for marker in CHALLENGE_MARKERS):
                    # Waiting out the read timeout on a verification page buys nothing: the content
                    # markers are never going to appear. Say so at once and give the cycle its
                    # seconds back.
                    raise RenderedPageChallenge(f"access verification page served for {url}")
                if time.time() > deadline:
                    break
                time.sleep(1.0)
            if any(marker in text for marker in CHALLENGE_MARKERS):
                raise RenderedPageChallenge(f"access verification page served for {url}")
            raise RuntimeError(f"rendered page did not show expected content within {timeout:.0f}s: {url}")
        finally:
            try:
                page.close()
            except Exception:
                pass


    def rendered_html(self, url: str, *, contains: tuple[str, ...] = (), timeout: float = 30.0) -> str:
        """Navigate and return the rendered DOM as HTML, once `contains` all appear in it.

        The text reader is enough for a price table, where the numbers are the content. A news index
        is different: the links ARE the content, and innerText throws every href away. Same launch,
        same read-only handling of what the vendor's browser rendered - only the property differs.
        """
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_SECONDS * 1000)
            deadline = time.time() + timeout
            markup = ""
            while True:
                try:
                    markup = page.content() or ""
                except Exception:
                    markup = ""
                if markup and all(marker in markup for marker in contains):
                    return markup
                if any(marker in markup for marker in CHALLENGE_MARKERS):
                    raise RenderedPageChallenge(f"access verification page served for {url}")
                if time.time() > deadline:
                    break
                time.sleep(1.0)
            if any(marker in markup for marker in CHALLENGE_MARKERS):
                raise RenderedPageChallenge(f"access verification page served for {url}")
            raise RuntimeError(f"rendered page did not show expected content within {timeout:.0f}s: {url}")
        finally:
            try:
                page.close()
            except Exception:
                pass


# Launch, read and a margin. The collector gives a source 90 seconds, and it used to abandon the
# worker thread at that point with Edge still on screen - which is how a verification page stayed up
# for an hour (Stefan, 18 September 2026).
JOIN_MARGIN_SECONDS = 50.0


def _join_or_close(worker: threading.Thread, profile: Path, timeout: float) -> None:
    """Wait for the read, and if it will not end, close the browser so it cannot outlive the call.

    Terminating the browser is also what unblocks the worker: its next Playwright call fails, the
    `with` block unwinds and the profile lock is released.
    """
    worker.join(timeout=timeout + JOIN_MARGIN_SECONDS)
    if not worker.is_alive():
        return
    kill_edge_for_profile(profile)
    worker.join(timeout=10.0)


def fetch_rendered_text(url: str, *, content_markers: tuple[str, ...] = (), any_markers: tuple[str, ...] = (),
                        timeout: float = 30.0, profile_dir: Path | None = None) -> str:
    """Launch off-screen Edge, read one page's visible text, close Edge.

    Runs in a worker thread so it is safe to call from code that already has an
    asyncio loop (FastAPI/Streamlit); Playwright's sync API refuses to run inside one.
    """
    until = challenged_until(url)
    if until is not None:
        raise RenderedPageChallenge(
            f"{_host_of(url)} served an access verification page; not retried before "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}")
    profile = Path(profile_dir) if profile_dir is not None else new_session_profile_dir()
    result: dict = {}

    def work() -> None:
        try:
            with EdgeCdpSession(profile_dir=profile) as session:
                result["text"] = session.rendered_text(
                    url, content_markers=content_markers, any_markers=any_markers, timeout=timeout
                )
        except BaseException as exc:  # propagate to caller thread
            result["error"] = exc

    t = threading.Thread(target=work, name="edge-cdp-fetch", daemon=True)
    t.start()
    _join_or_close(t, profile, timeout)
    if "error" in result:
        if isinstance(result["error"], RenderedPageChallenge):
            record_challenge(url)
        raise result["error"]
    if "text" not in result:
        raise EdgeUnavailable(f"the rendered read did not finish and Edge was closed: {url}")
    return result["text"]


def fetch_rendered_html(url: str, *, contains: tuple[str, ...] = (), timeout: float = 30.0,
                        profile_dir: Path | None = None) -> str:
    """Launch off-screen Edge, read one page's rendered HTML, close Edge.

    Same worker-thread arrangement as `fetch_rendered_text`, for the same reason: Playwright's sync
    API refuses to run inside an existing asyncio loop.
    """
    until = challenged_until(url)
    if until is not None:
        raise RenderedPageChallenge(
            f"{_host_of(url)} served an access verification page; not retried before "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}")
    profile = Path(profile_dir) if profile_dir is not None else new_session_profile_dir()
    result: dict = {}

    def work() -> None:
        try:
            with EdgeCdpSession(profile_dir=profile) as session:
                result["html"] = session.rendered_html(url, contains=contains, timeout=timeout)
        except BaseException as exc:  # propagate to caller thread
            result["error"] = exc

    t = threading.Thread(target=work, name="edge-cdp-fetch-html", daemon=True)
    t.start()
    _join_or_close(t, profile, timeout)
    if "error" in result:
        if isinstance(result["error"], RenderedPageChallenge):
            record_challenge(url)
        raise result["error"]
    if "html" not in result:
        raise EdgeUnavailable(f"the rendered read did not finish and Edge was closed: {url}")
    return result["html"]
