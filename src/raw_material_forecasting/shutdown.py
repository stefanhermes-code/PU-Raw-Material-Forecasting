"""Closing the application from the browser, so nothing is left running behind it.

Stefan, 26 September 2026: "that is not an elegant solution. Come back with something more elegant
in the browser."

The launcher starts three processes that are not children of its console - the market-data engine,
the collection run and the delivery worker - each detached, each with its PID written to a file. A
Ctrl+C in the console reaches none of them. Only the `taskkill` lines at the end of the batch file
do, and those lines are only reached by answering N to a prompt that should never have had to
appear.

This module does what those lines do, from inside the application, so the person closes the thing
they are looking at. Streamlit then exits of its own accord, the batch file stops waiting on it,
runs its own cleanup over an already-clean slate, and the console window closes by itself. No
prompt, no orphans.

Two rules it will not break:

  A PID is only a number, and the operating system reuses numbers. Nothing is terminated until the
  process at that PID has been confirmed to be the one we started, by looking at its command line.
  Killing a stranger's process because a stale file named its PID would be worse than leaving ours
  running.

  A pid file that names nothing is cleared rather than reported as a failure. The process is gone,
  which is the outcome that was wanted.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

# The three processes the launcher starts detached, the file each PID is written to, and the text
# that must appear in the process's command line before we are willing to terminate it.
RUNTIME_PROCESSES: tuple[tuple[str, str, str], ...] = (
    ("Delivery worker", "PUForecast_delivery_scheduler.pid",
     "raw_material_forecasting.scheduled_delivery_worker"),
    ("Collection run", "PUForecast_market_data_collect.pid", "collect_step6_background"),
    ("Market-data engine", "PUForecast_market_data_engine.pid", "app.main:app"),
)


# Left behind when the person closes the application on purpose, so the launcher can tell a
# deliberate stop from a crash. Stefan, 27 September 2026: "this stop button does not really work
# very well" - it worked, and then the console window sat there saying the MVP had stopped with an
# error and waiting for a keypress. Closing the application terminates Streamlit, Windows reports a
# terminated process as a failure, and the launcher believed it. A real crash still stops and waits
# so the message can be read; this says the stop was asked for.
CLOSED_ON_PURPOSE = "PUForecast_closed_on_purpose"


def say_the_close_was_asked_for(*, directory: Path | None = None) -> Path:
    """Leave the marker the launcher looks for. Returns where it was written."""
    marker = (directory or runtime_dir()) / CLOSED_ON_PURPOSE
    marker.write_text("closed from the application\n", encoding="ascii")
    return marker


@dataclass(frozen=True)
class StopResult:
    """What happened to one of the launcher's processes."""

    name: str
    pid: int | None
    outcome: str      # stopped | not running | not ours | no pid file
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome in ("stopped", "not running", "no pid file")


def runtime_dir() -> Path:
    """Where the launcher writes its pid files: the user's TEMP."""
    return Path(tempfile.gettempdir())


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    try:
        return int(text.split()[0]) if text else None
    except (ValueError, IndexError):
        return None


def _command_line(pid: int) -> str | None:
    """The command line of a running process, or None if there is no such process.

    psutil where it is installed; otherwise PowerShell, which is how the launcher asks the same
    question. Neither is required for the module to import, so a machine with neither simply
    refuses to terminate anything, which is the safe direction.
    """
    try:
        import psutil  # noqa: PLC0415 - optional, and only on this path
    except ImportError:
        pass
    else:
        try:
            return " ".join(psutil.Process(pid).cmdline())
        except Exception:
            return None
    if os.name != "nt":
        return None
    import subprocess  # noqa: PLC0415

    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'"
             f" -ErrorAction SilentlyContinue).CommandLine"],
            capture_output=True, text=True, timeout=10, check=False)
    except Exception:
        return None
    line = (out.stdout or "").strip()
    return line or None


def _terminate(pid: int) -> None:
    import signal  # noqa: PLC0415

    os.kill(pid, signal.SIGTERM)


def stop_runtime(*, directory: Path | None = None,
                 command_line: Callable[[int], str | None] = _command_line,
                 terminate: Callable[[int], None] = _terminate,
                 processes: Iterable[tuple[str, str, str]] = RUNTIME_PROCESSES,
                 ) -> list[StopResult]:
    """Stop everything the launcher started detached, newest dependency first."""
    directory = directory or runtime_dir()
    results: list[StopResult] = []
    for name, filename, marker in processes:
        path = directory / filename
        if not path.exists():
            results.append(StopResult(name=name, pid=None, outcome="no pid file"))
            continue
        pid = _read_pid(path)
        if pid is None:
            path.unlink(missing_ok=True)
            results.append(StopResult(name=name, pid=None, outcome="no pid file",
                                      detail="the pid file was empty"))
            continue
        line = command_line(pid)
        if line is None:
            path.unlink(missing_ok=True)
            results.append(StopResult(name=name, pid=pid, outcome="not running"))
            continue
        if marker not in line:
            # The number was reused by something else. Leave it alone and say so.
            results.append(StopResult(name=name, pid=pid, outcome="not ours",
                                      detail="that process id now belongs to something else"))
            continue
        try:
            terminate(pid)
        except Exception as error:  # the process may exit between the check and the signal
            results.append(StopResult(name=name, pid=pid, outcome="not running",
                                      detail=str(error)))
        else:
            results.append(StopResult(name=name, pid=pid, outcome="stopped"))
        path.unlink(missing_ok=True)
    return results


def everything_stopped(results: Iterable[StopResult]) -> bool:
    return all(result.ok for result in results)
