from __future__ import annotations

from pymongo import MongoClient
from pymongo.database import Database

from biolink_backend.config.settings import Settings
from biolink_backend.shared.logging import get_logger

logger = get_logger(__name__)


class MongoConnectionProvider:
    """Proveedor oficial de cliente y pool PyMongo hacia MongoDB Community 8.0.38."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: MongoClient | None = None

    def open(self) -> None:
        if self._client is not None:
            return
        self._client = MongoClient(
            self._settings.mongodb_uri,
            minPoolSize=self._settings.mongodb_min_pool_size,
            maxPoolSize=self._settings.mongodb_max_pool_size,
            connectTimeoutMS=self._settings.mongodb_connect_timeout_ms,
            serverSelectionTimeoutMS=self._settings.mongodb_server_selection_timeout_ms,
            socketTimeoutMS=self._settings.mongodb_socket_timeout_ms,
            appname=self._settings.app_name,
        )
        logger.info(
            "Cliente de MongoDB inicializado",
            extra={
                "uri": self._settings.mongodb_masked_uri,
                "max_pool_size": self._settings.mongodb_max_pool_size,
            },
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.info("Cliente de MongoDB cerrado")

    @property
    def client(self) -> MongoClient:
        if self._client is None:
            self.open()
        assert self._client is not None
        return self._client

    def get_database(self) -> Database:
        return self.client[self._settings.mongodb_db]
