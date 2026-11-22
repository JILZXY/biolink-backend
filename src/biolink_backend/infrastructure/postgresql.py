from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from biolink_backend.config.settings import Settings
from biolink_backend.shared.logging import get_logger

logger = get_logger(__name__)


class PostgresConnectionProvider:
    """Proveedor de pool nativo Psycopg 3 hacia PostgreSQL 18.7 (sin ORM)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: ConnectionPool | None = None

    def open(self) -> None:
        if self._pool is not None:
            return
        timeout_sec = max(1, int(round(self._settings.postgres_connect_timeout_sec)))
        kwargs = {
            "connect_timeout": timeout_sec,
            "options": f"-c statement_timeout={self._settings.postgres_statement_timeout_ms}",
            "row_factory": dict_row,
        }
        # open=False permite crear el pool sin bloquear si la base inicia despues
        self._pool = ConnectionPool(
            conninfo=self._settings.postgres_dsn,
            min_size=self._settings.postgres_pool_min_size,
            max_size=self._settings.postgres_pool_max_size,
            timeout=self._settings.postgres_connect_timeout_sec,
            reconnect_timeout=max(1.0, self._settings.postgres_connect_timeout_sec),
            kwargs=kwargs,
            open=False,
        )
        logger.info(
            "Pool de PostgreSQL inicializado",
            extra={
                "dsn": self._settings.postgres_masked_dsn,
                "min_size": self._settings.postgres_pool_min_size,
                "max_size": self._settings.postgres_pool_max_size,
            },
        )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close(timeout=1.0)
            self._pool = None
            logger.info("Pool de PostgreSQL cerrado")

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        if self._pool is None:
            self.open()
        assert self._pool is not None
        self._pool.open(wait=False)
        with self._pool.connection(timeout=self._settings.postgres_connect_timeout_sec) as conn:
            yield conn
