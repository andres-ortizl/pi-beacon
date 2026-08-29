from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def protect_database_files(database_path: Path) -> None:
    parent_exists = database_path.parent.exists()
    database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent_exists or database_path.parent.name == "pi-beacon":
        os.chmod(database_path.parent, 0o700)
    for path in (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
    ):
        if path.exists():
            os.chmod(path, 0o600)


def migration_config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    database_url = f"sqlite:///{database_path}".replace("%", "%%")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def upgrade_database(database_path: Path) -> None:
    protect_database_files(database_path)
    command.upgrade(migration_config(database_path), "head")
    protect_database_files(database_path)
