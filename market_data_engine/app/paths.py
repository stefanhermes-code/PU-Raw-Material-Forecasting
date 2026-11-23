"""Where the engine's files actually are.

The engine runs with its working directory set to `market_data_engine/`, because that is where
`alembic.ini` and the `app` package live. Every data path written as `data/...` therefore resolved to
`market_data_engine/data/...` rather than to the repository's own `data/` folder - so the historical
price import and the reference packs both looked in a folder that does not exist, found nothing, and
said nothing. Four releases reported "imported" for a file that was never opened.

Paths are resolved from this module's own location instead, which does not depend on how the process
was started.
"""
from __future__ import annotations

from pathlib import Path

# app/paths.py -> app -> market_data_engine -> repository root
ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent


def repo_path(*parts: str) -> Path:
    """A path inside the repository, wherever the process happens to be running from."""
    return REPO_ROOT.joinpath(*parts)
