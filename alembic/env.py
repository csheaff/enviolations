"""Alembic environment configuration.

This project uses raw sqlite3 (no SQLAlchemy ORM), so autogenerate is not
available. All migrations are authored manually.

SQLite-specific: render_as_batch=True enables batch mode, which recreates
tables behind the scenes to work around SQLite's limited ALTER TABLE support.
"""
from __future__ import annotations

import logging
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import MetaData, engine_from_config, event, pool, text

config = context.config

if config.config_file_name is not None:
    # Preserve root logger level — fileConfig resets it to WARN per alembic.ini,
    # which breaks caplog-based tests that depend on INFO propagation through root.
    _saved_root_level = logging.getLogger().level
    fileConfig(config.config_file_name, disable_existing_loggers=False)
    logging.getLogger().setLevel(_saved_root_level)

# Naming convention ensures SQLite batch ALTER operations can resolve unnamed
# constraints (foreign keys, indexes, etc.) during table-recreation migrations.
_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
target_metadata = MetaData(naming_convention=_convention)


def _get_url() -> str:
    """Build the SQLite URL from ENVIOLATIONS_DB_PATH or fall back to default."""
    db_path = os.environ.get("ENVIOLATIONS_DB_PATH", "data/pipeline.db")
    # Resolve relative paths against the repo/app root
    p = Path(db_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return f"sqlite:///{p}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    @event.listens_for(connectable, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
