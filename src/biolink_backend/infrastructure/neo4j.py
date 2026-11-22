from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from neo4j import Driver, GraphDatabase, Session

from biolink_backend.config.settings import Settings
from biolink_backend.shared.logging import get_logger

logger = get_logger(__name__)


class Neo4jConnectionProvider:
    """Proveedor oficial del driver Bolt de Neo4j Community 2026.10.2."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver: Driver | None = None

    def open(self) -> None:
        if self._driver is not None:
            return
        self._driver = GraphDatabase.driver(
            self._settings.neo4j_uri,
            auth=(self._settings.neo4j_user, self._settings.neo4j_password.get_secret_value()),
            max_connection_pool_size=self._settings.neo4j_max_connection_pool_size,
            connection_timeout=self._settings.neo4j_connection_timeout_sec,
            max_transaction_retry_time=self._settings.neo4j_max_transaction_retry_time_sec,
        )
        logger.info(
            "Driver Bolt de Neo4j inicializado",
            extra={
                "uri": self._settings.neo4j_uri,
                "max_connection_pool_size": self._settings.neo4j_max_connection_pool_size,
            },
        )

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
            logger.info("Driver Bolt de Neo4j cerrado")

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            self.open()
        assert self._driver is not None
        return self._driver

    @contextmanager
    def session(self, *, database: str | None = None) -> Iterator[Session]:
        with self.driver.session(database=database) as session:
            yield session
