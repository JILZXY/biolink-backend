from __future__ import annotations

from typing import Any

from neo4j.exceptions import DriverError, Neo4jError

from biolink_backend.application.dto import GraphAssertionEdgeDTO
from biolink_backend.infrastructure.neo4j import Neo4jConnectionProvider
from biolink_backend.shared.exceptions import DatabaseUnavailableError
from biolink_backend.shared.time import partial_date_upper_bound


def _map_edge(row: dict[str, Any]) -> GraphAssertionEdgeDTO:
    return GraphAssertionEdgeDTO(
        assertion_id=str(row["assertion_id"]),
        actor_taxon_id=str(row["actor_taxon_id"]),
        target_taxon_id=str(row["target_taxon_id"]),
        interaction_type_code=str(row["interaction_type_code"]),
        locality_id=str(row["locality_id"]),
        period_start=str(row["period_start"]),
        period_end=str(row["period_end"]),
        season_label=str(row.get("season_label") or ""),
        method_code=str(row["method_code"]),
        source_ids=[str(s) for s in (row.get("source_ids") or []) if s is not None],
    )


class Neo4jPublicNetworkRepository:
    """Repositorio Cypher para consultas publicas sobre afirmaciones PUBLISHED en Neo4j."""

    def __init__(self, provider: Neo4jConnectionProvider) -> None:
        self._provider = provider

    @staticmethod
    def _build_assertion_filters(
        *,
        interaction_type_code: str | None = None,
        locality_id: str | None = None,
        protected_status: str | None = None,
        spatial_validation_status: str | None = None,
        method_code: str | None = None,
        source_id: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        include_undetermined_locality: bool = True,
    ) -> tuple[list[str], dict[str, Any]]:
        clauses = ["a.status = 'PUBLISHED'"]
        params: dict[str, Any] = {}

        if interaction_type_code:
            clauses.append("a.interaction_type_code = $interaction_type_code")
            params["interaction_type_code"] = interaction_type_code
        if locality_id:
            clauses.append("loc.locality_id = $locality_id")
            params["locality_id"] = locality_id
        if protected_status:
            clauses.append("loc.protected_status = $protected_status")
            params["protected_status"] = protected_status
        elif not include_undetermined_locality:
            clauses.append("NOT coalesce(loc.protected_status, 'UNDETERMINED') IN ['UNDETERMINED', 'NOT_REPORTED']")
        if spatial_validation_status:
            clauses.append("loc.spatial_validation_status = $spatial_validation_status")
            params["spatial_validation_status"] = spatial_validation_status
        if method_code:
            clauses.append("a.method_code = $method_code")
            params["method_code"] = method_code
        if source_id:
            clauses.append("EXISTS { MATCH (a)-[:SUPPORTED_BY]->(:Evidence)-[:CITED_FROM]->(:Source {source_id: $source_id}) }")
            params["source_id"] = source_id
        if period_start:
            clauses.append("a.period_end >= $period_start")
            params["period_start"] = period_start
        if period_end:
            clauses.append("a.period_start <= $period_end_upper")
            params["period_end_upper"] = partial_date_upper_bound(period_end)

        return clauses, params

    def list_taxon_interactions(
        self,
        *,
        taxon_id: str,
        role: str,
        interaction_type_code: str | None,
        locality_id: str | None,
        method_code: str | None,
        period_start: str | None,
        period_end: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[GraphAssertionEdgeDTO], int]:
        clauses, params = self._build_assertion_filters(
            interaction_type_code=interaction_type_code,
            locality_id=locality_id,
            method_code=method_code,
            period_start=period_start,
            period_end=period_end,
            include_undetermined_locality=True,
        )
        params["taxon_id"] = taxon_id
        if role == "actor":
            clauses.append("actor.taxon_id = $taxon_id")
        elif role == "target":
            clauses.append("target.taxon_id = $taxon_id")
        else:
            clauses.append("(actor.taxon_id = $taxon_id OR target.taxon_id = $taxon_id)")

        where_sql = " AND ".join(clauses)
        skip = (page - 1) * page_size
        params["skip"] = skip
        params["limit"] = page_size

        count_cypher = f"""
        MATCH (actor:Taxon)-[:ACTOR_IN]->(a:InteractionAssertion)-[:TARGETS]->(target:Taxon),
              (a)-[:OBSERVED_AT]->(loc:Locality)
        WHERE {where_sql}
        RETURN count(DISTINCT a) AS total;
        """
        data_cypher = f"""
        MATCH (actor:Taxon)-[:ACTOR_IN]->(a:InteractionAssertion)-[:TARGETS]->(target:Taxon),
              (a)-[:OBSERVED_AT]->(loc:Locality)
        WHERE {where_sql}
        WITH DISTINCT a, actor, target, loc
        ORDER BY a.assertion_id ASC
        SKIP $skip LIMIT $limit
        OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(:Evidence)-[:CITED_FROM]->(src:Source)
        RETURN
            a.assertion_id AS assertion_id,
            actor.taxon_id AS actor_taxon_id,
            target.taxon_id AS target_taxon_id,
            a.interaction_type_code AS interaction_type_code,
            loc.locality_id AS locality_id,
            a.period_start AS period_start,
            a.period_end AS period_end,
            a.season_label AS season_label,
            a.method_code AS method_code,
            collect(DISTINCT src.source_id) AS source_ids
        ORDER BY assertion_id ASC;
        """
        try:
            with self._provider.session() as session:
                total_rec = session.run(count_cypher, params).single()
                total = int(total_rec["total"]) if total_rec else 0
                if total == 0:
                    return [], 0
                rows = session.run(data_cypher, params).data()
        except (Neo4jError, DriverError) as exc:
            raise DatabaseUnavailableError("neo4j", str(exc)) from exc

        return [_map_edge(r) for r in rows], total

    def is_assertion_projected(self, assertion_id: str) -> bool:
        cypher = """
        MATCH (a:InteractionAssertion {assertion_id: $assertion_id, status: 'PUBLISHED'})
        RETURN count(a) > 0 AS projected;
        """
        try:
            with self._provider.session() as session:
                rec = session.run(cypher, {"assertion_id": assertion_id}).single()
        except (Neo4jError, DriverError) as exc:
            raise DatabaseUnavailableError("neo4j", str(exc)) from exc
        return bool(rec["projected"]) if rec else False

    def get_filtered_subgraph(
        self,
        *,
        taxon_id: str | None,
        locality_id: str | None,
        protected_status: str | None,
        spatial_validation_status: str | None,
        interaction_type_code: str | None,
        method_code: str | None,
        source_id: str | None,
        period_start: str | None,
        period_end: str | None,
        include_undetermined_locality: bool,
        max_depth: int,
        limit: int,
    ) -> list[GraphAssertionEdgeDTO]:
        clauses, params = self._build_assertion_filters(
            interaction_type_code=interaction_type_code,
            locality_id=locality_id,
            protected_status=protected_status,
            spatial_validation_status=spatial_validation_status,
            method_code=method_code,
            source_id=source_id,
            period_start=period_start,
            period_end=period_end,
            include_undetermined_locality=include_undetermined_locality,
        )
        params["limit"] = limit

        if taxon_id is not None:
            params["taxon_id"] = taxon_id
            if max_depth <= 1:
                clauses.append("(actor.taxon_id = $taxon_id OR target.taxon_id = $taxon_id)")
                cypher_prefix = ""
            else:
                # Cada salto ecologico cruza Taxon -> InteractionAssertion -> Taxon (2 aristas)
                raw_hops = max_depth * 2
                cypher_prefix = f"""
                MATCH p = (seed:Taxon {{taxon_id: $taxon_id}})-[:ACTOR_IN|TARGETS*1..{raw_hops}]-(reached:Taxon)
                WHERE ALL(n IN nodes(p) WHERE NOT n:InteractionAssertion OR n.status = 'PUBLISHED')
                WITH collect(DISTINCT reached.taxon_id) + [$taxon_id] AS reachable_ids
                """
                clauses.append("(actor.taxon_id IN reachable_ids AND target.taxon_id IN reachable_ids)")
        else:
            cypher_prefix = ""

        where_sql = " AND ".join(clauses)
        cypher = f"""
        {cypher_prefix}
        MATCH (actor:Taxon)-[:ACTOR_IN]->(a:InteractionAssertion)-[:TARGETS]->(target:Taxon),
              (a)-[:OBSERVED_AT]->(loc:Locality)
        WHERE {where_sql}
        WITH DISTINCT a, actor, target, loc
        ORDER BY a.assertion_id ASC
        LIMIT $limit
        OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(:Evidence)-[:CITED_FROM]->(src:Source)
        RETURN
            a.assertion_id AS assertion_id,
            actor.taxon_id AS actor_taxon_id,
            target.taxon_id AS target_taxon_id,
            a.interaction_type_code AS interaction_type_code,
            loc.locality_id AS locality_id,
            a.period_start AS period_start,
            a.period_end AS period_end,
            a.season_label AS season_label,
            a.method_code AS method_code,
            collect(DISTINCT src.source_id) AS source_ids
        ORDER BY assertion_id ASC;
        """
        try:
            with self._provider.session() as session:
                rows = session.run(cypher, params).data()
        except (Neo4jError, DriverError) as exc:
            raise DatabaseUnavailableError("neo4j", str(exc)) from exc

        return [_map_edge(r) for r in rows]

    def get_locality_assertions(
        self,
        *,
        locality_id: str,
        interaction_type_code: str | None,
        period_start: str | None,
        period_end: str | None,
    ) -> list[GraphAssertionEdgeDTO]:
        clauses, params = self._build_assertion_filters(
            locality_id=locality_id,
            interaction_type_code=interaction_type_code,
            period_start=period_start,
            period_end=period_end,
            include_undetermined_locality=True,
        )
        where_sql = " AND ".join(clauses)
        cypher = f"""
        MATCH (actor:Taxon)-[:ACTOR_IN]->(a:InteractionAssertion)-[:TARGETS]->(target:Taxon),
              (a)-[:OBSERVED_AT]->(loc:Locality)
        WHERE {where_sql}
        WITH DISTINCT a, actor, target, loc
        OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(:Evidence)-[:CITED_FROM]->(src:Source)
        RETURN
            a.assertion_id AS assertion_id,
            actor.taxon_id AS actor_taxon_id,
            target.taxon_id AS target_taxon_id,
            a.interaction_type_code AS interaction_type_code,
            loc.locality_id AS locality_id,
            a.period_start AS period_start,
            a.period_end AS period_end,
            a.season_label AS season_label,
            a.method_code AS method_code,
            collect(DISTINCT src.source_id) AS source_ids
        ORDER BY assertion_id ASC;
        """
        try:
            with self._provider.session() as session:
                rows = session.run(cypher, params).data()
        except (Neo4jError, DriverError) as exc:
            raise DatabaseUnavailableError("neo4j", str(exc)) from exc

        return [_map_edge(r) for r in rows]

    def get_taxon_connectivity_edges(
        self,
        *,
        taxon_id: str,
        locality_id: str | None,
        interaction_type_code: str | None,
        period_start: str | None,
        period_end: str | None,
    ) -> list[GraphAssertionEdgeDTO]:
        clauses, params = self._build_assertion_filters(
            locality_id=locality_id,
            interaction_type_code=interaction_type_code,
            period_start=period_start,
            period_end=period_end,
            include_undetermined_locality=True,
        )
        params["taxon_id"] = taxon_id
        clauses.append("(actor.taxon_id = $taxon_id OR target.taxon_id = $taxon_id)")
        where_sql = " AND ".join(clauses)
        cypher = f"""
        MATCH (actor:Taxon)-[:ACTOR_IN]->(a:InteractionAssertion)-[:TARGETS]->(target:Taxon),
              (a)-[:OBSERVED_AT]->(loc:Locality)
        WHERE {where_sql}
        WITH DISTINCT a, actor, target, loc
        OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(:Evidence)-[:CITED_FROM]->(src:Source)
        RETURN
            a.assertion_id AS assertion_id,
            actor.taxon_id AS actor_taxon_id,
            target.taxon_id AS target_taxon_id,
            a.interaction_type_code AS interaction_type_code,
            loc.locality_id AS locality_id,
            a.period_start AS period_start,
            a.period_end AS period_end,
            a.season_label AS season_label,
            a.method_code AS method_code,
            collect(DISTINCT src.source_id) AS source_ids
        ORDER BY assertion_id ASC;
        """
        try:
            with self._provider.session() as session:
                rows = session.run(cypher, params).data()
        except (Neo4jError, DriverError) as exc:
            raise DatabaseUnavailableError("neo4j", str(exc)) from exc

        return [_map_edge(r) for r in rows]
