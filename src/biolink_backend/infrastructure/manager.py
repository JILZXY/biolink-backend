from __future__ import annotations

from biolink_backend.config.settings import Settings
from biolink_backend.infrastructure.mongodb import MongoConnectionProvider
from biolink_backend.infrastructure.neo4j import Neo4jConnectionProvider
from biolink_backend.infrastructure.postgresql import PostgresConnectionProvider
from biolink_backend.shared.logging import get_logger

logger = get_logger(__name__)


class DatabaseConnectionManager:
    """Ciclo de vida unificado de conexiones hacia PostgreSQL, MongoDB y Neo4j."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.postgres = PostgresConnectionProvider(settings)
        self.mongodb = MongoConnectionProvider(settings)
        self.neo4j = Neo4jConnectionProvider(settings)

    def startup(self) -> None:
        """Registra la preparacion de proveedores con inicializacion perezosa (lazy).

        Evita abrir sockets o hilos de fondo antes de que un endpoint o worker
        requiera realmente el motor correspondiente.
        """
        logger.info(
            "Proveedores de bases de datos listos (inicializacion perezosa)",
            extra={"budget": self.settings.memory_budget_summary()},
        )

    def shutdown(self) -> None:
        logger.info("Cerrando pools de conexiones de bases de datos")
        for closer in (self.neo4j.close, self.mongodb.close, self.postgres.close):
            try:
                closer()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error controlado durante el cierre de proveedor", extra={"error": str(exc)})
