from __future__ import annotations

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def create_database_engine(path: str | Path) -> Engine:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", future=True)
