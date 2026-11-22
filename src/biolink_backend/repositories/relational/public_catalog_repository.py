from __future__ import annotations

import psycopg

from biolink_backend.application.dto import (
    EvidenceDetailDTO,
    InteractionListItemDTO,
    LocalitySummaryDTO,
    OccurrenceItemDTO,
    PaginatedResult,
    SourceSummaryDTO,
    SourceVersionDTO,
    TaxonIdentifierDTO,
    TaxonNameDTO,
    TaxonSummaryDTO,
)
from biolink_backend.infrastructure.postgresql import PostgresConnectionProvider
from biolink_backend.shared.exceptions import DatabaseUnavailableError
from biolink_backend.shared.time import partial_date_upper_bound

_TAXON_SORT_COLUMNS: dict[str, str] = {
    "accepted_name": "accepted_name",
    "taxon_id": "taxon_id",
    "taxonomic_rank": "taxonomic_rank",
}

_OCCURRENCE_SORT_COLUMNS: dict[str, str] = {
    "occurrence_id": "o.occurrence_id",
    "event_date": "o.event_date",
    "accepted_name": "t.accepted_name",
}


class PostgresPublicCatalogRepository:
    """Repositorio SQL parametrizado (sin ORM) para consultas publicas en PostgreSQL 18.7."""

    def __init__(self, provider: PostgresConnectionProvider) -> None:
        self._provider = provider

    def search_taxa(
        self,
        *,
        query: str | None,
        taxonomic_rank: str | None,
        organism_group: str | None,
        taxonomic_status: str | None,
        sort_by: str = "accepted_name",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult[TaxonSummaryDTO]:
        if sort_by not in _TAXON_SORT_COLUMNS:
            raise ValueError(f"Campo de ordenamiento no permitido: '{sort_by}'.")
        direction = "DESC" if sort_order.lower() == "desc" else "ASC"
        sort_col = _TAXON_SORT_COLUMNS[sort_by]
        offset = (page - 1) * page_size

        conditions: list[str] = []
        params: list[object] = []

        cleaned_q = query.strip() if query else None
        if cleaned_q:
            like_pattern = f"%{cleaned_q}%"
            conditions.append(
                """(
                    t.accepted_name ILIKE %s
                    OR EXISTS (
                        SELECT 1 FROM taxon_name tn_f
                        WHERE tn_f.taxon_id = t.taxon_id
                          AND (tn_f.name ILIKE %s OR tn_f.verbatim_name ILIKE %s)
                    )
                    OR EXISTS (
                        SELECT 1 FROM taxon_identifier ti_f
                        WHERE ti_f.taxon_id = t.taxon_id
                          AND (ti_f.external_id ILIKE %s OR concat(ti_f.scheme, ':', ti_f.external_id) ILIKE %s)
                    )
                )"""
            )
            params.extend([like_pattern, like_pattern, like_pattern, like_pattern, like_pattern])

        if taxonomic_rank:
            conditions.append("t.taxonomic_rank = %s")
            params.append(taxonomic_rank)
        if organism_group:
            conditions.append("t.organism_group = %s")
            params.append(organism_group)
        if taxonomic_status:
            conditions.append("t.taxonomic_status = %s")
            params.append(taxonomic_status)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        match_select = """
            NULL::varchar AS matched_name,
            NULL::varchar AS matched_usage,
            NULL::varchar AS matched_verbatim_name,
            NULL::varchar AS matched_identifier
        """
        match_params: list[object] = []
        if cleaned_q:
            like_pattern = f"%{cleaned_q}%"
            match_select = """
                COALESCE(
                    (
                        SELECT tn_m.name FROM taxon_name tn_m
                        WHERE tn_m.taxon_id = t.taxon_id
                          AND (tn_m.name ILIKE %s OR tn_m.verbatim_name ILIKE %s)
                        ORDER BY
                            CASE
                                WHEN lower(tn_m.name) = lower(%s) THEN 0
                                WHEN tn_m.name_usage = 'SYNONYM' THEN 1
                                WHEN tn_m.name_usage = 'ACCEPTED' THEN 2
                                ELSE 3
                            END,
                            tn_m.taxon_name_id
                        LIMIT 1
                    ),
                    t.accepted_name
                ) AS matched_name,
                (
                    SELECT tn_m.name_usage FROM taxon_name tn_m
                    WHERE tn_m.taxon_id = t.taxon_id
                      AND (tn_m.name ILIKE %s OR tn_m.verbatim_name ILIKE %s)
                    ORDER BY
                        CASE
                            WHEN lower(tn_m.name) = lower(%s) THEN 0
                            WHEN tn_m.name_usage = 'SYNONYM' THEN 1
                            WHEN tn_m.name_usage = 'ACCEPTED' THEN 2
                            ELSE 3
                        END,
                        tn_m.taxon_name_id
                    LIMIT 1
                ) AS matched_usage,
                (
                    SELECT tn_m.verbatim_name FROM taxon_name tn_m
                    WHERE tn_m.taxon_id = t.taxon_id
                      AND (tn_m.name ILIKE %s OR tn_m.verbatim_name ILIKE %s)
                    ORDER BY
                        CASE
                            WHEN lower(tn_m.name) = lower(%s) THEN 0
                            WHEN tn_m.name_usage = 'SYNONYM' THEN 1
                            ELSE 2
                        END,
                        tn_m.taxon_name_id
                    LIMIT 1
                ) AS matched_verbatim_name,
                (
                    SELECT concat(ti_m.scheme, ':', ti_m.external_id) FROM taxon_identifier ti_m
                    WHERE ti_m.taxon_id = t.taxon_id
                      AND (ti_m.external_id ILIKE %s OR concat(ti_m.scheme, ':', ti_m.external_id) ILIKE %s)
                    ORDER BY ti_m.taxon_identifier_id
                    LIMIT 1
                ) AS matched_identifier
            """
            match_params = [
                like_pattern, like_pattern, cleaned_q,
                like_pattern, like_pattern, cleaned_q,
                like_pattern, like_pattern, cleaned_q,
                like_pattern, like_pattern,
            ]

        sql = f"""
        WITH matched_taxa AS (
            SELECT
                t.taxon_id,
                t.accepted_name,
                t.scientific_authorship,
                t.taxonomic_rank,
                t.organism_group,
                t.taxonomic_status,
                {match_select}
            FROM taxon t
            {where_clause}
        )
        SELECT *, COUNT(*) OVER() AS total_count
        FROM matched_taxa
        ORDER BY {sort_col} {direction}, taxon_id ASC
        LIMIT %s OFFSET %s;
        """
        full_params = [*match_params, *params, page_size, offset]
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, full_params)
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        total = int(rows[0]["total_count"]) if rows else 0
        items = [
            TaxonSummaryDTO(
                taxon_id=r["taxon_id"],
                accepted_name=r["accepted_name"],
                scientific_authorship=r["scientific_authorship"],
                taxonomic_rank=r["taxonomic_rank"],
                organism_group=r["organism_group"],
                taxonomic_status=r["taxonomic_status"],
                matched_name=r["matched_name"],
                matched_usage=r["matched_usage"],
                matched_verbatim_name=r["matched_verbatim_name"],
                matched_identifier=r["matched_identifier"],
            )
            for r in rows
        ]
        return PaginatedResult(items=items, total=total, page=page, page_size=page_size)

    def get_taxon_by_id(self, taxon_id: str) -> TaxonSummaryDTO | None:
        return self.get_taxa_by_ids([taxon_id]).get(taxon_id)

    def get_taxa_by_ids(self, taxon_ids: list[str]) -> dict[str, TaxonSummaryDTO]:
        if not taxon_ids:
            return {}
        sql = """
        SELECT taxon_id, accepted_name, scientific_authorship, taxonomic_rank, organism_group, taxonomic_status
        FROM taxon
        WHERE taxon_id = ANY(%s);
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (list(set(taxon_ids)),))
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return {
            r["taxon_id"]: TaxonSummaryDTO(
                taxon_id=r["taxon_id"],
                accepted_name=r["accepted_name"],
                scientific_authorship=r["scientific_authorship"],
                taxonomic_rank=r["taxonomic_rank"],
                organism_group=r["organism_group"],
                taxonomic_status=r["taxonomic_status"],
            )
            for r in rows
        }

    def get_taxon_names(self, taxon_id: str) -> list[TaxonNameDTO]:
        sql = """
        SELECT taxon_name_id, name, name_usage, language, source_id, is_preferred, verbatim_name
        FROM taxon_name
        WHERE taxon_id = %s
        ORDER BY is_preferred DESC, name_usage ASC, name ASC;
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (taxon_id,))
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return [
            TaxonNameDTO(
                taxon_name_id=r["taxon_name_id"],
                name=r["name"],
                name_usage=r["name_usage"],
                language=r["language"],
                source_id=r["source_id"],
                is_preferred=bool(r["is_preferred"]),
                verbatim_name=r["verbatim_name"],
            )
            for r in rows
        ]

    def get_taxon_identifiers(self, taxon_id: str) -> list[TaxonIdentifierDTO]:
        sql = """
        SELECT taxon_identifier_id, scheme, external_id, source_id
        FROM taxon_identifier
        WHERE taxon_id = %s
        ORDER BY scheme ASC, external_id ASC;
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (taxon_id,))
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return [
            TaxonIdentifierDTO(
                taxon_identifier_id=r["taxon_identifier_id"],
                scheme=r["scheme"],
                external_id=r["external_id"],
                source_id=r["source_id"],
            )
            for r in rows
        ]

    def get_locality_by_id(self, locality_id: str) -> LocalitySummaryDTO | None:
        return self.get_localities_by_ids([locality_id]).get(locality_id)

    def get_localities_by_ids(self, locality_ids: list[str]) -> dict[str, LocalitySummaryDTO]:
        if not locality_ids:
            return {}
        sql = """
        SELECT
            locality_id, name, locality_type, protected_status,
            spatial_validation_status, coordinate_precision, parent_locality_id
        FROM locality
        WHERE locality_id = ANY(%s);
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (list(set(locality_ids)),))
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return {
            r["locality_id"]: LocalitySummaryDTO(
                locality_id=r["locality_id"],
                name=r["name"],
                locality_type=r["locality_type"],
                protected_status=r["protected_status"],
                spatial_validation_status=r["spatial_validation_status"],
                coordinate_precision=r["coordinate_precision"],
                parent_locality_id=r["parent_locality_id"],
            )
            for r in rows
        }

    def get_published_assertion_by_id(self, assertion_id: str) -> InteractionListItemDTO | None:
        return self.get_published_assertions_by_ids([assertion_id]).get(assertion_id)

    def get_published_assertions_by_ids(
        self, assertion_ids: list[str]
    ) -> dict[str, InteractionListItemDTO]:
        if not assertion_ids:
            return {}
        sql = """
        SELECT
            ia.assertion_id,
            ia.status,
            it.code AS interaction_type_code,
            ia.period_start,
            ia.period_end,
            ia.season_label,
            ia.method_code,
            ia.assertion_scope,
            act.taxon_id AS actor_id,
            act.accepted_name AS actor_name,
            act.scientific_authorship AS actor_auth,
            act.taxonomic_rank AS actor_rank,
            act.organism_group AS actor_group,
            act.taxonomic_status AS actor_status,
            tgt.taxon_id AS target_id,
            tgt.accepted_name AS target_name,
            tgt.scientific_authorship AS target_auth,
            tgt.taxonomic_rank AS target_rank,
            tgt.organism_group AS target_group,
            tgt.taxonomic_status AS target_status,
            loc.locality_id,
            loc.name AS locality_name,
            loc.locality_type,
            loc.protected_status,
            loc.spatial_validation_status,
            loc.coordinate_precision,
            loc.parent_locality_id,
            COALESCE(
                array_agg(DISTINCT s.citation_text) FILTER (WHERE s.citation_text IS NOT NULL),
                ARRAY[]::text[]
            ) AS source_citations,
            COALESCE(
                array_agg(DISTINCT e.source_locator) FILTER (WHERE e.source_locator IS NOT NULL),
                ARRAY[]::text[]
            ) AS source_locators,
            COALESCE(
                array_agg(DISTINCT e.interpretation_limit) FILTER (WHERE e.interpretation_limit IS NOT NULL),
                ARRAY[]::text[]
            ) AS interpretation_limits
        FROM interaction_assertion ia
        JOIN interaction_type it ON it.interaction_type_id = ia.interaction_type_id
        JOIN taxon act ON act.taxon_id = ia.actor_taxon_id
        JOIN taxon tgt ON tgt.taxon_id = ia.target_taxon_id
        JOIN locality loc ON loc.locality_id = ia.locality_id
        LEFT JOIN assertion_evidence ae ON ae.assertion_id = ia.assertion_id
        LEFT JOIN evidence e ON e.evidence_id = ae.evidence_id
        LEFT JOIN source s ON s.source_id = e.source_id
        WHERE ia.assertion_id = ANY(%s)
          AND ia.status = 'PUBLISHED'
        GROUP BY ia.assertion_id, it.code, act.taxon_id, tgt.taxon_id, loc.locality_id;
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (list(set(assertion_ids)),))
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        result: dict[str, InteractionListItemDTO] = {}
        for r in rows:
            result[r["assertion_id"]] = InteractionListItemDTO(
                assertion_id=r["assertion_id"],
                status=r["status"],
                interaction_type_code=r["interaction_type_code"],
                actor_taxon=TaxonSummaryDTO(
                    taxon_id=r["actor_id"],
                    accepted_name=r["actor_name"],
                    scientific_authorship=r["actor_auth"],
                    taxonomic_rank=r["actor_rank"],
                    organism_group=r["actor_group"],
                    taxonomic_status=r["actor_status"],
                ),
                target_taxon=TaxonSummaryDTO(
                    taxon_id=r["target_id"],
                    accepted_name=r["target_name"],
                    scientific_authorship=r["target_auth"],
                    taxonomic_rank=r["target_rank"],
                    organism_group=r["target_group"],
                    taxonomic_status=r["target_status"],
                ),
                locality=LocalitySummaryDTO(
                    locality_id=r["locality_id"],
                    name=r["locality_name"],
                    locality_type=r["locality_type"],
                    protected_status=r["protected_status"],
                    spatial_validation_status=r["spatial_validation_status"],
                    coordinate_precision=r["coordinate_precision"],
                    parent_locality_id=r["parent_locality_id"],
                ),
                period_start=r["period_start"],
                period_end=r["period_end"],
                season_label=r["season_label"],
                method_code=r["method_code"],
                assertion_scope=r["assertion_scope"],
                source_citations=list(r["source_citations"]),
                source_locators=list(r["source_locators"]),
                interpretation_limits=list(r["interpretation_limits"]),
            )
        return result

    def get_evidences_for_assertion(self, assertion_id: str) -> list[EvidenceDetailDTO]:
        sql = """
        SELECT
            e.evidence_id,
            e.source_id,
            e.source_version_id,
            e.source_locator,
            e.evidence_kind,
            e.method_code,
            e.reported_term,
            e.summary,
            e.interpretation_limit,
            e.period_start,
            e.period_end,
            e.license_id,
            ae.evidence_role,
            ae.review_note,
            s.source_type,
            s.title,
            s.authors,
            s.publication_year,
            s.citation_text,
            s.license_id AS source_license_id,
            s.doi,
            s.uri,
            s.publisher
        FROM interaction_assertion ia
        JOIN assertion_evidence ae ON ae.assertion_id = ia.assertion_id
        JOIN evidence e ON e.evidence_id = ae.evidence_id
        JOIN source s ON s.source_id = e.source_id
        WHERE ia.assertion_id = %s
          AND ia.status = 'PUBLISHED'
        ORDER BY
            CASE ae.evidence_role WHEN 'PRIMARY' THEN 0 WHEN 'SUPPORTING' THEN 1 ELSE 2 END,
            e.evidence_id;
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (assertion_id,))
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return [
            EvidenceDetailDTO(
                evidence_id=r["evidence_id"],
                source_id=r["source_id"],
                source_version_id=r["source_version_id"],
                source_locator=r["source_locator"],
                evidence_kind=r["evidence_kind"],
                method_code=r["method_code"],
                reported_term=r["reported_term"],
                summary=r["summary"],
                interpretation_limit=r["interpretation_limit"],
                period_start=r["period_start"],
                period_end=r["period_end"],
                license_id=r["license_id"],
                evidence_role=r["evidence_role"],
                review_note=r["review_note"],
                source=SourceSummaryDTO(
                    source_id=r["source_id"],
                    source_type=r["source_type"],
                    title=r["title"],
                    authors=r["authors"],
                    publication_year=int(r["publication_year"]),
                    citation_text=r["citation_text"],
                    license_id=r["source_license_id"],
                    doi=r["doi"],
                    uri=r["uri"],
                    publisher=r["publisher"],
                ),
            )
            for r in rows
        ]

    def count_undetermined_published_assertions(
        self,
        *,
        period_start: str | None = None,
        period_end: str | None = None,
        interaction_type_code: str | None = None,
        method_code: str | None = None,
        source_id: str | None = None,
    ) -> int:
        conditions = [
            "ia.status = 'PUBLISHED'",
            "loc.protected_status IN ('UNDETERMINED', 'NOT_REPORTED')",
        ]
        params: list[object] = []
        if period_start:
            conditions.append("ia.period_end >= %s")
            params.append(period_start)
        if period_end:
            conditions.append("ia.period_start <= %s")
            params.append(partial_date_upper_bound(period_end))
        if interaction_type_code:
            conditions.append("it.code = %s")
            params.append(interaction_type_code)
        if method_code:
            conditions.append("ia.method_code = %s")
            params.append(method_code)
        if source_id:
            conditions.append(
                "EXISTS (SELECT 1 FROM assertion_evidence ae JOIN evidence e ON e.evidence_id = ae.evidence_id "
                "WHERE ae.assertion_id = ia.assertion_id AND e.source_id = %s)"
            )
            params.append(source_id)

        sql = f"""
        SELECT COUNT(*) AS cnt
        FROM interaction_assertion ia
        JOIN locality loc ON loc.locality_id = ia.locality_id
        JOIN interaction_type it ON it.interaction_type_id = ia.interaction_type_id
        WHERE {' AND '.join(conditions)};
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc
        return int(row["cnt"]) if row else 0

    def get_source_by_id(self, source_id: str) -> SourceSummaryDTO | None:
        sql = """
        SELECT source_id, source_type, title, authors, publication_year, citation_text, license_id, doi, uri, publisher
        FROM source
        WHERE source_id = %s;
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (source_id,))
                    r = cur.fetchone()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        if not r:
            return None
        return SourceSummaryDTO(
            source_id=r["source_id"],
            source_type=r["source_type"],
            title=r["title"],
            authors=r["authors"],
            publication_year=int(r["publication_year"]),
            citation_text=r["citation_text"],
            license_id=r["license_id"],
            doi=r["doi"],
            uri=r["uri"],
            publisher=r["publisher"],
        )

    def get_source_versions(self, source_id: str) -> list[SourceVersionDTO]:
        sql = """
        SELECT
            source_version_id, provider_version, release_date, retrieved_at,
            endpoint_or_file, query_parameters, filters, license_id, checksum
        FROM source_version
        WHERE source_id = %s
        ORDER BY retrieved_at DESC, source_version_id ASC;
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (source_id,))
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return [
            SourceVersionDTO(
                source_version_id=r["source_version_id"],
                provider_version=r["provider_version"],
                release_date=r["release_date"].isoformat() if r["release_date"] else None,
                retrieved_at=r["retrieved_at"].isoformat(),
                endpoint_or_file=r["endpoint_or_file"],
                query_parameters=dict(r["query_parameters"]),
                filters=dict(r["filters"]),
                license_id=r["license_id"],
                checksum=r["checksum"],
            )
            for r in rows
        ]

    def get_source_usage_counts(self, source_id: str) -> tuple[int, int]:
        sql = """
        SELECT
            (SELECT COUNT(*) FROM evidence WHERE source_id = %s) AS evidences_count,
            (
                SELECT COUNT(DISTINCT ia.assertion_id)
                FROM interaction_assertion ia
                JOIN assertion_evidence ae ON ae.assertion_id = ia.assertion_id
                JOIN evidence e ON e.evidence_id = ae.evidence_id
                WHERE e.source_id = %s
                  AND ia.status = 'PUBLISHED'
            ) AS published_assertions_count;
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (source_id, source_id))
                    row = cur.fetchone()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        if not row:
            return 0, 0
        return int(row["published_assertions_count"]), int(row["evidences_count"])

    def list_occurrences(
        self,
        *,
        taxon_id: str | None,
        locality_id: str | None,
        source_id: str | None,
        georeference_status: str | None,
        date_from: str | None,
        date_to: str | None,
        sort_by: str = "occurrence_id",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult[OccurrenceItemDTO]:
        if sort_by not in _OCCURRENCE_SORT_COLUMNS:
            raise ValueError(f"Campo de ordenamiento no permitido para ocurrencias: '{sort_by}'.")
        direction = "DESC" if sort_order.lower() == "desc" else "ASC"
        sort_col = _OCCURRENCE_SORT_COLUMNS[sort_by]
        offset = (page - 1) * page_size

        conditions: list[str] = []
        params: list[object] = []

        if taxon_id:
            conditions.append("o.taxon_id = %s")
            params.append(taxon_id)
        if locality_id:
            conditions.append("o.locality_id = %s")
            params.append(locality_id)
        if source_id:
            conditions.append("o.source_id = %s")
            params.append(source_id)
        if georeference_status:
            conditions.append("o.georeference_status = %s")
            params.append(georeference_status)
        if date_from:
            conditions.append("o.event_date >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("o.event_date <= %s")
            params.append(partial_date_upper_bound(date_to))

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        sql = f"""
        SELECT
            o.occurrence_id,
            o.source_record_id,
            o.event_date,
            o.latitude,
            o.longitude,
            o.coordinate_uncertainty_meters,
            o.basis_of_record,
            o.georeference_status,
            t.taxon_id,
            t.accepted_name,
            t.scientific_authorship,
            t.taxonomic_rank,
            t.organism_group,
            t.taxonomic_status,
            s.source_id,
            s.source_type,
            s.title AS source_title,
            s.authors AS source_authors,
            s.publication_year,
            s.citation_text,
            s.license_id AS source_license_id,
            s.doi,
            s.uri,
            s.publisher,
            l.locality_id,
            l.name AS locality_name,
            l.locality_type,
            l.protected_status,
            l.spatial_validation_status,
            l.coordinate_precision,
            l.parent_locality_id,
            COUNT(*) OVER() AS total_count
        FROM occurrence o
        JOIN taxon t ON t.taxon_id = o.taxon_id
        JOIN source s ON s.source_id = o.source_id
        LEFT JOIN locality l ON l.locality_id = o.locality_id
        {where_clause}
        ORDER BY {sort_col} {direction}, o.occurrence_id ASC
        LIMIT %s OFFSET %s;
        """
        full_params = [*params, page_size, offset]
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, full_params)
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        total = int(rows[0]["total_count"]) if rows else 0
        items: list[OccurrenceItemDTO] = []
        for r in rows:
            loc = None
            if r["locality_id"] is not None:
                loc = LocalitySummaryDTO(
                    locality_id=r["locality_id"],
                    name=r["locality_name"],
                    locality_type=r["locality_type"],
                    protected_status=r["protected_status"],
                    spatial_validation_status=r["spatial_validation_status"],
                    coordinate_precision=r["coordinate_precision"],
                    parent_locality_id=r["parent_locality_id"],
                )
            items.append(
                OccurrenceItemDTO(
                    occurrence_id=r["occurrence_id"],
                    taxon=TaxonSummaryDTO(
                        taxon_id=r["taxon_id"],
                        accepted_name=r["accepted_name"],
                        scientific_authorship=r["scientific_authorship"],
                        taxonomic_rank=r["taxonomic_rank"],
                        organism_group=r["organism_group"],
                        taxonomic_status=r["taxonomic_status"],
                    ),
                    source=SourceSummaryDTO(
                        source_id=r["source_id"],
                        source_type=r["source_type"],
                        title=r["source_title"],
                        authors=r["source_authors"],
                        publication_year=int(r["publication_year"]),
                        citation_text=r["citation_text"],
                        license_id=r["source_license_id"],
                        doi=r["doi"],
                        uri=r["uri"],
                        publisher=r["publisher"],
                    ),
                    locality=loc,
                    source_record_id=r["source_record_id"],
                    event_date=r["event_date"],
                    latitude=float(r["latitude"]) if r["latitude"] is not None else None,
                    longitude=float(r["longitude"]) if r["longitude"] is not None else None,
                    coordinate_uncertainty_meters=(
                        float(r["coordinate_uncertainty_meters"])
                        if r["coordinate_uncertainty_meters"] is not None
                        else None
                    ),
                    basis_of_record=r["basis_of_record"],
                    georeference_status=r["georeference_status"],
                    is_interaction_evidence=False,
                )
            )
        return PaginatedResult(items=items, total=total, page=page, page_size=page_size)
