"""Alembic environment: the tables of claims_pipeline/store/models.py, on the database
named by CLAIMS_DATABASE_URL (or ``alembic -x url=...``).

    alembic upgrade head                                  # bring the database up to date
    alembic revision --autogenerate -m "what changed"     # after editing store/models.py
"""
from logging.config import fileConfig

from alembic import context

from claims_pipeline.settings import Settings
from claims_pipeline.store.models import Base, Timestamp
from claims_pipeline.store.repository import connect

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
URL = context.get_x_argument(as_dictionary=True).get("url") or config.get_main_option("sqlalchemy.url") \
    or Settings().database_url


def render_item(type_, obj, autogen_context):
    """Migrations stand on their own: a Timestamp is written as the String it is stored as."""
    if type_ == "type" and isinstance(obj, Timestamp):
        return "sa.String()"
    return False


def run_migrations_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, render_as_batch=True,
                      render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with connect(URL).connect() as connection:
        # batch mode: SQLite alters a table by copying it, which Alembic does for us
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True,
                          render_item=render_item)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
