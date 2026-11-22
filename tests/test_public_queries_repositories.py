from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from neo4j.exceptions import ServiceUnavailable
from pymongo.errors import PyMongoError

from biolink_backend.repositories.document.public_dossier_repository import MongoPublicDossierRepository
from biolink_backend.repositories.graph.public_network_repository import Neo4jPublicNetworkRepository
from biolink_backend.repositories.relational.public_catalog_repository import PostgresPublicCatalogRepository
from biolink_backend.shared.exceptions import DatabaseUnavailableError


class _RecordingCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.executed: list[tuple[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _FakePgConn:
    def __init__(self, cursor: _RecordingCursor) -> None:
        self._cur = cursor

    def cursor(self) -> _RecordingCursor:
        return self._cur


class _FakePostgresProvider:
    def __init__(self, cursor: _RecordingCursor | None = None, fail_exc: Exception | None = None) -> None:
        self._cursor = cursor or _RecordingCursor()
        self._fail_exc = fail_exc

    @contextmanager
    def connection(self):
        if self._fail_exc:
            raise self._fail_exc
        yield _FakePgConn(self._cursor)


def test_postgres_catalog_repository_queries_and_error_mapping() -> None:
    cur = _RecordingCursor(
        rows=[
            {
                "taxon_id": "TAX_AN_001",
                "accepted_name": "Artibeus lituratus",
                "scientific_authorship": "(Olfers, 1818)",
                "taxonomic_rank": "SPECIES",
                "organism_group": "ANIMAL",
                "taxonomic_status": "ACCEPTED",
                "matched_name": "Artibeus lituratus",
                "matched_usage": "ACCEPTED",
                "matched_verbatim_name": None,
                "matched_identifier": None,
                "total_count": 1,
            }
        ]
    )
    repo = PostgresPublicCatalogRepository(_FakePostgresProvider(cur))  # type: ignore[arg-type]
    res = repo.search_taxa(
        query="Artibeus",
        taxonomic_rank="SPECIES",
        organism_group="ANIMAL",
        taxonomic_status=None,
        sort_by="accepted_name",
        sort_order="asc",
        page=1,
        page_size=20,
    )
    assert res.total == 1
    assert "ORDER BY accepted_name ASC" in cur.executed[0][0]

    # Rechazar campo sort_by no permitido (proteccion contra inyeccion SQL)
    with pytest.raises(ValueError):
        repo.search_taxa(
            query=None, taxonomic_rank=None, organism_group=None, taxonomic_status=None,
            sort_by="accepted_name; DROP TABLE taxon;--",
        )

    # Error de Psycopg se mapea a DatabaseUnavailableError (503)
    bad_repo = PostgresPublicCatalogRepository(
        _FakePostgresProvider(fail_exc=psycopg.OperationalError("connection failed"))  # type: ignore[arg-type]
    )
    with pytest.raises(DatabaseUnavailableError):
        bad_repo.get_taxon_by_id("TAX_AN_001")


def test_mongo_dossier_repository_license_redaction_and_errors() -> None:
    class _FakeCollection:
        def __init__(self, doc: dict[str, Any] | None = None, fail: bool = False) -> None:
            self._doc = doc
            self._fail = fail

        def find_one(self, _query: dict[str, Any]) -> dict[str, Any] | None:
            if self._fail:
                raise PyMongoError("timeout")
            return self._doc

        def count_documents(self, _query: dict[str, Any]) -> int:
            return 1

    class _FakeDb(dict):
        pass

    class _FakeMongoProvider:
        def __init__(self, db) -> None:
            self._db = db

        def get_database(self):
            return self._db

    raw_doc_restricted = {
        "_id": "RAW_1",
        "import_batch_id": "BAT_1",
        "source_id": "BAT2007",
        "external_record_id": "AST_SCI_001",
        "provider": "BAT2007",
        "checksum": "a" * 64,
        "raw_payload": {"secret_full_text": "do not expose"},
        "license_metadata": {"license_id": "ACADEMIC_CITATION_ONLY", "redistribution_permitted": False},
    }
    db = _FakeDb({"raw_source_record": _FakeCollection(raw_doc_restricted)})
    repo = MongoPublicDossierRepository(_FakeMongoProvider(db))  # type: ignore[arg-type]
    ref = repo.get_raw_source_record_by_external_id("BAT2007", "AST_SCI_001")
    assert ref is not None
    assert ref.redistribution_permitted is False
    assert ref.raw_payload is None
    assert ref.checksum == "a" * 64

    fail_db = _FakeDb({"taxon_dossier": _FakeCollection(fail=True), "taxon_dossier_versions": _FakeCollection()})
    fail_repo = MongoPublicDossierRepository(_FakeMongoProvider(fail_db))  # type: ignore[arg-type]
    with pytest.raises(DatabaseUnavailableError):
        fail_repo.get_taxon_dossier("TAX_AN_001")


def test_neo4j_network_repository_cypher_filters_and_errors() -> None:
    class _FakeNeoResult:
        def __init__(self, data_rows=None) -> None:
            self._rows = data_rows or []

        def data(self):
            return self._rows

    class _RecordingSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def run(self, cypher: str, params: dict[str, Any]):
            self.calls.append((cypher, params))
            return _FakeNeoResult([])

    class _FakeNeoProvider:
        def __init__(self, sess: _RecordingSession | None = None, fail: bool = False) -> None:
            self._sess = sess or _RecordingSession()
            self._fail = fail

        @contextmanager
        def session(self, **_kw):
            if self._fail:
                raise ServiceUnavailable("bolt connection down")
            yield self._sess

    sess = _RecordingSession()
    repo = Neo4jPublicNetworkRepository(_FakeNeoProvider(sess))  # type: ignore[arg-type]
    repo.get_filtered_subgraph(
        taxon_id="TAX_AN_001",
        locality_id=None,
        protected_status=None,
        spatial_validation_status=None,
        interaction_type_code=None,
        method_code=None,
        source_id=None,
        period_start="2004",
        period_end="2014",
        include_undetermined_locality=False,
        max_depth=2,
        limit=50,
    )
    cypher, params = sess.calls[0]
    assert "a.status = 'PUBLISHED'" in cypher
    assert "NOT coalesce(loc.protected_status, 'UNDETERMINED') IN ['UNDETERMINED', 'NOT_REPORTED']" in cypher
    assert "*1..4" in cypher
    assert params["period_end_upper"] == "2014-12-31"

    fail_repo = Neo4jPublicNetworkRepository(_FakeNeoProvider(fail=True))  # type: ignore[arg-type]
    with pytest.raises(DatabaseUnavailableError):
        fail_repo.is_assertion_projected("AST_SCI_001")
