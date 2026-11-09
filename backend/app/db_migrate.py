"""Database migrations (Alembic) run on startup.

Schema changes are migration files in migrations/versions/, so a database is upgraded in
place instead of being recreated. Create one after changing app/models.py:

    .venv/bin/alembic revision --autogenerate -m "what changed"

tests/test_core.py fails if the models and the migrations ever disagree.
"""

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.config import BACKEND_ROOT

BASELINE = "0001"


def alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return config


def migrate(engine: Engine) -> None:
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        if "alembic_version" not in tables and "claims" in tables:
            # Created by create_all before migrations existed: it matches the baseline.
            command.stamp(config, BASELINE)
        command.upgrade(config, "head")
