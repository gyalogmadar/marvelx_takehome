"""Opening the store: one engine per database URL, migrated to the latest schema first."""
from __future__ import annotations

from functools import cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from .repository import connect

PROJECT = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    cfg = Config(str(PROJECT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade(url: str) -> None:
    """``alembic upgrade head`` on ``url``."""
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(url), "head")


@cache
def sessions(url: str) -> sessionmaker:
    """Sessions on ``url``, which is brought up to the latest migration on first use."""
    upgrade(url)
    return sessionmaker(connect(url), expire_on_commit=False)
