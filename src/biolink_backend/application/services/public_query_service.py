from __future__ import annotations

from collections import defaultdict
from typing import Any

from biolink_backend.application.dto import (
    DISCLAIMER_CONNECTIVITY_NOT_ABUNDANCE,
    DISCLAIMER_FRUGIVORY_NO_DISPERSAL,
    DISCLAIMER_LOCALITY_SEPARATION,
    DISCLAIMER_OCCURRENCE_NOT_INTERACTION,
    ConnectivityResultDTO,
    GraphAssertionEdgeDTO,
    InteractionDetailResultDTO,
    InteractionListItemDTO,
    LocalityComparisonResultDTO,
    LocalityNetworkMetricsDTO,
    LocalitySummaryDTO,
    OccurrenceItemDTO,
    PaginatedResult,
    PartnerConnectivityDTO,
    RawSourceReferenceDTO,
    SharedInteractionPairDTO,
    SourceDetailResultDTO,
    SubgraphEdgeDTO,
    SubgraphNodeDTO,
    SubgraphResultDTO,
    TaxonDetailResultDTO,
    TaxonSummaryDTO,
)
from biolink_backend.application.ports import (
    CatalogRelationalPort,
    DossierDocumentPort,
    NetworkGraphPort,
)
from biolink_backend.domain.rules import validate_interaction_type_for_v1
from biolink_backend.domain.value_objects import PartialDateRange
from biolink_backend.domain.vocabularies import (
    EvidenceMethodCode,
    InteractionTypeCode,
    OrganismGroup,
    ProtectedStatus,
    SpatialValidationStatus,
    TaxonomicRank,
    TaxonomicStatus,
)
from biolink_backend.shared.exceptions import (
    BusinessRuleViolationError,
    DomainValidationError,
    EntityNotFoundError,
)
from biolink_backend.shared.time import validate_partial_iso_date


def _validate_period_filters(period_start: str | None, period_end: str | None) -> tuple[str | None, str | None]:
    if period_start and period_end:
        r = PartialDateRange(period_start, period_end)
        return r.period_start, r.period_end
    cleaned_start = None
    cleaned_end = None
    try:
        if period_start:
            cleaned_start = validate_partial_iso_date(period_start)
        if period_end:
            cleaned_end = validate_partial_iso_date(period_end)
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc
    return cleaned_start, cleaned_end


def _validate_interaction_filter(interaction_type_code: str | None) -> str | None:
    if not interaction_type_code:
        return None
    try:
        enum_val = InteractionTypeCode(interaction_type_code)
    except ValueError as exc:
        raise DomainValidationError(f"Tipo de interaccion invalido: '{interaction_type_code}'.") from exc
    validate_interaction_type_for_v1(enum_val)
    return enum_val.value


def _validate_method_filter(method_code: str | None) -> str | None:
    if not method_code:
        return None
    try:
        return EvidenceMethodCode(method_code).value
    except ValueError as exc:
        raise DomainValidationError(f"Codigo de metodo de evidencia invalido: '{method_code}'.") from exc


def _validate_pagination(page: int, page_size: int) -> None:
    if page < 1:
        raise DomainValidationError("El numero de pagina debe ser mayor o igual a 1.")
    if page_size < 1 or page_size > 100:
        raise DomainValidationError("El tamano de pagina debe estar entre 1 y 100.")


class PublicQueryService:
    """Servicio de aplicacion coordinador para operaciones publicas OP-01..OP-08 y OP-15."""

    def __init__(
        self,
        *,
        relational: CatalogRelationalPort,
        document: DossierDocumentPort,
        graph: NetworkGraphPort,
    ) -> None:
        self._relational = relational
        self._document = document
        self._graph = graph

    # ------------------------------------------------------------------
    # OP-01: Buscar taxones
    # ------------------------------------------------------------------
    def search_taxa(
        self,
        *,
        query: str | None = None,
        taxonomic_rank: str | None = None,
        organism_group: str | None = None,
        taxonomic_status: str | None = None,
        sort_by: str = "accepted_name",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult[TaxonSummaryDTO]:
        _validate_pagination(page, page_size)
        if taxonomic_rank:
            try:
                taxonomic_rank = TaxonomicRank(taxonomic_rank).value
            except ValueError as exc:
                raise DomainValidationError(f"Rango taxonomico invalido: '{taxonomic_rank}'.") from exc
        if organism_group:
            try:
                organism_group = OrganismGroup(organism_group).value
            except ValueError as exc:
                raise DomainValidationError(f"Grupo biologico invalido: '{organism_group}'.") from exc
        if taxonomic_status:
            try:
                taxonomic_status = TaxonomicStatus(taxonomic_status).value
            except ValueError as exc:
                raise DomainValidationError(f"Estado taxonomico invalido: '{taxonomic_status}'.") from exc

        return self._relational.search_taxa(
            query=query,
            taxonomic_rank=taxonomic_rank,
            organism_group=organism_group,
            taxonomic_status=taxonomic_status,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            page_size=page_size,
        )

    # ------------------------------------------------------------------
    # OP-02: Consultar ficha de taxon (PostgreSQL + MongoDB)
    # ------------------------------------------------------------------
    def get_taxon_fiche(self, taxon_id: str) -> TaxonDetailResultDTO:
        taxon = self._relational.get_taxon_by_id(taxon_id)
        if taxon is None:
            raise EntityNotFoundError("Taxon", taxon_id)

        names = self._relational.get_taxon_names(taxon_id)
        identifiers = self._relational.get_taxon_identifiers(taxon_id)
        # No ocultar fallo de MongoDB si ocurre (propaga DatabaseUnavailableError -> 503)
        dossier = self._document.get_taxon_dossier(taxon_id)

        warnings: list[str] = []
        if dossier is None:
            warnings.append(
                "No existe ficha descriptiva documental registrada para este taxon; "
                "se presentan unicamente la identidad canonica y nombres de la fuente relacional."
            )
        if taxon.taxonomic_rank in (
            TaxonomicRank.GENUS.value,
            TaxonomicRank.MORPHOSPECIES.value,
            TaxonomicRank.UNRESOLVED.value,
        ):
            warnings.append(
                f"Resolucion taxonomica conservada a nivel '{taxon.taxonomic_rank}' "
                "conforme a la fuente original (RD-10)."
            )

        return TaxonDetailResultDTO(
            taxon=taxon,
            names=names,
            external_identifiers=identifiers,
            has_dossier=dossier is not None,
            dossier=dossier,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # OP-03: Listar interacciones de un taxon (Neo4j + PostgreSQL)
    # ------------------------------------------------------------------
    def list_taxon_interactions(
        self,
        *,
        taxon_id: str,
        role: str = "any",
        interaction_type_code: str | None = None,
        locality_id: str | None = None,
        method_code: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult[InteractionListItemDTO]:
        _validate_pagination(page, page_size)
        if role not in ("any", "actor", "target"):
            raise DomainValidationError("El filtro 'role' debe ser 'any', 'actor' o 'target'.")

        taxon = self._relational.get_taxon_by_id(taxon_id)
        if taxon is None:
            raise EntityNotFoundError("Taxon", taxon_id)

        if locality_id and self._relational.get_locality_by_id(locality_id) is None:
            raise EntityNotFoundError("Locality", locality_id)

        it_code = _validate_interaction_filter(interaction_type_code)
        m_code = _validate_method_filter(method_code)
        p_start, p_end = _validate_period_filters(period_start, period_end)

        graph_edges, total = self._graph.list_taxon_interactions(
            taxon_id=taxon_id,
            role=role,
            interaction_type_code=it_code,
            locality_id=locality_id,
            method_code=m_code,
            period_start=p_start,
            period_end=p_end,
            page=page,
            page_size=page_size,
        )
        if not graph_edges:
            return PaginatedResult(items=[], total=total, page=page, page_size=page_size)

        # Doble validacion autoritativa contra PostgreSQL: solo afirmaciones PUBLISHED vigentes
        assertion_ids = [edge.assertion_id for edge in graph_edges]
        published_map = self._relational.get_published_assertions_by_ids(assertion_ids)
        items = [published_map[aid] for aid in assertion_ids if aid in published_map]
        return PaginatedResult(items=items, total=total, page=page, page_size=page_size)

    # ------------------------------------------------------------------
    # OP-04: Consultar detalle y evidencia de interaccion (PG + Mongo + Neo4j)
    # ------------------------------------------------------------------
    def get_interaction_detail(self, assertion_id: str) -> InteractionDetailResultDTO:
        assertion = self._relational.get_published_assertion_by_id(assertion_id)
        if assertion is None:
            raise EntityNotFoundError("InteractionAssertion", assertion_id)

        evidences = self._relational.get_evidences_for_assertion(assertion_id)
        if not evidences:
            raise BusinessRuleViolationError(
                "RD-02",
                f"La afirmacion '{assertion_id}' carece de evidencia revisada verificable.",
            )

        source_ids = sorted({ev.source_id for ev in evidences})
        study_dossiers = self._document.get_study_dossiers_by_source_ids(source_ids)
        raw_refs: list[RawSourceReferenceDTO] = []
        for sid in source_ids:
            ref = self._document.get_raw_source_record_by_external_id(sid, assertion_id)
            if ref is not None:
                raw_refs.append(ref)

        projected = self._graph.is_assertion_projected(assertion_id)
        warnings = [DISCLAIMER_FRUGIVORY_NO_DISPERSAL, DISCLAIMER_LOCALITY_SEPARATION]
        if not projected:
            warnings.append(
                "Afirmacion publicada en fuente relacional pero pendiente de sincronizacion en el grafo Neo4j."
            )

        return InteractionDetailResultDTO(
            assertion=assertion,
            evidences=evidences,
            study_dossiers=study_dossiers,
            raw_source_references=raw_refs,
            graph_projected=projected,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # OP-05: Filtrar subred documentada (Neo4j + PostgreSQL)
    # ------------------------------------------------------------------
    def filter_documented_subgraph(
        self,
        *,
        taxon_id: str | None = None,
        locality_id: str | None = None,
        protected_status: str | None = None,
        spatial_validation_status: str | None = None,
        interaction_type_code: str | None = None,
        method_code: str | None = None,
        source_id: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        max_depth: int = 1,
        limit: int = 100,
    ) -> SubgraphResultDTO:
        if max_depth < 1 or max_depth > 3:
            raise DomainValidationError("La profundidad 'max_depth' debe estar entre 1 y 3.")
        if limit < 1 or limit > 200:
            raise DomainValidationError("El limite 'limit' de afirmaciones debe estar entre 1 y 200.")

        if taxon_id and self._relational.get_taxon_by_id(taxon_id) is None:
            raise EntityNotFoundError("Taxon", taxon_id)

        locality_obj: LocalitySummaryDTO | None = None
        if locality_id:
            locality_obj = self._relational.get_locality_by_id(locality_id)
            if locality_obj is None:
                raise EntityNotFoundError("Locality", locality_id)

        if protected_status:
            try:
                protected_status = ProtectedStatus(protected_status).value
            except ValueError as exc:
                raise DomainValidationError(f"Estado de proteccion invalido: '{protected_status}'.") from exc
        if spatial_validation_status:
            try:
                spatial_validation_status = SpatialValidationStatus(spatial_validation_status).value
            except ValueError as exc:
                raise DomainValidationError(
                    f"Estado de validacion espacial invalido: '{spatial_validation_status}'."
                ) from exc

        it_code = _validate_interaction_filter(interaction_type_code)
        m_code = _validate_method_filter(method_code)
        p_start, p_end = _validate_period_filters(period_start, period_end)

        explicit_undetermined = protected_status in (
            ProtectedStatus.UNDETERMINED.value,
            ProtectedStatus.NOT_REPORTED.value,
        ) or (
            locality_obj is not None
            and locality_obj.protected_status
            in (ProtectedStatus.UNDETERMINED.value, ProtectedStatus.NOT_REPORTED.value)
        )

        graph_edges = self._graph.get_filtered_subgraph(
            taxon_id=taxon_id,
            locality_id=locality_id,
            protected_status=protected_status,
            spatial_validation_status=spatial_validation_status,
            interaction_type_code=it_code,
            method_code=m_code,
            source_id=source_id,
            period_start=p_start,
            period_end=p_end,
            include_undetermined_locality=explicit_undetermined,
            max_depth=max_depth,
            limit=limit,
        )

        # Confirmar estado PUBLISHED vigente desde PostgreSQL y enriquecer nombres canonicos
        assertion_ids = [e.assertion_id for e in graph_edges]
        published_map = self._relational.get_published_assertions_by_ids(assertion_ids)
        valid_assertions = [published_map[aid] for aid in assertion_ids if aid in published_map]

        nodes_map: dict[str, SubgraphNodeDTO] = {}
        interactions: list[SubgraphEdgeDTO] = []
        edge_sources: dict[str, list[str]] = {e.assertion_id: e.source_ids for e in graph_edges}

        for item in valid_assertions:
            act = item.actor_taxon
            tgt = item.target_taxon
            loc = item.locality

            nodes_map[act.taxon_id] = SubgraphNodeDTO(
                node_id=act.taxon_id,
                node_type="Taxon",
                label=act.accepted_name,
                properties={
                    "organism_group": act.organism_group,
                    "taxonomic_rank": act.taxonomic_rank,
                    "taxonomic_status": act.taxonomic_status,
                },
            )
            nodes_map[tgt.taxon_id] = SubgraphNodeDTO(
                node_id=tgt.taxon_id,
                node_type="Taxon",
                label=tgt.accepted_name,
                properties={
                    "organism_group": tgt.organism_group,
                    "taxonomic_rank": tgt.taxonomic_rank,
                    "taxonomic_status": tgt.taxonomic_status,
                },
            )
            nodes_map[loc.locality_id] = SubgraphNodeDTO(
                node_id=loc.locality_id,
                node_type="Locality",
                label=loc.name,
                properties={
                    "locality_type": loc.locality_type,
                    "protected_status": loc.protected_status,
                    "spatial_validation_status": loc.spatial_validation_status,
                },
            )
            interactions.append(
                SubgraphEdgeDTO(
                    assertion_id=item.assertion_id,
                    actor_taxon_id=act.taxon_id,
                    actor_name=act.accepted_name,
                    target_taxon_id=tgt.taxon_id,
                    target_name=tgt.accepted_name,
                    interaction_type_code=item.interaction_type_code,
                    locality_id=loc.locality_id,
                    locality_name=loc.name,
                    protected_status=loc.protected_status,
                    period_start=item.period_start,
                    period_end=item.period_end,
                    method_code=item.method_code,
                    source_ids=edge_sources.get(item.assertion_id, []),
                )
            )

        undetermined_excluded = 0
        if not explicit_undetermined and locality_id is None and protected_status is None:
            undetermined_excluded = self._relational.count_undetermined_published_assertions(
                period_start=p_start,
                period_end=p_end,
                interaction_type_code=it_code,
                method_code=m_code,
                source_id=source_id,
            )

        applied_filters: dict[str, Any] = {
            k: v
            for k, v in {
                "taxon_id": taxon_id,
                "locality_id": locality_id,
                "protected_status": protected_status,
                "spatial_validation_status": spatial_validation_status,
                "interaction_type_code": it_code,
                "method_code": m_code,
                "source_id": source_id,
                "period_start": p_start,
                "period_end": p_end,
                "max_depth": max_depth,
                "limit": limit,
            }.items()
            if v is not None
        }

        warnings = [DISCLAIMER_LOCALITY_SEPARATION, DISCLAIMER_FRUGIVORY_NO_DISPERSAL]
        if undetermined_excluded > 0:
            warnings.append(
                f"Se excluyeron {undetermined_excluded} afirmaciones publicadas con localidad "
                "indeterminada para no mezclarlas con localidades verificadas."
            )

        return SubgraphResultDTO(
            applied_filters=applied_filters,
            nodes=sorted(nodes_map.values(), key=lambda n: (n.node_type, n.node_id)),
            interactions=interactions,
            undetermined_locality_excluded_count=undetermined_excluded,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # OP-06: Comparar localidades (Neo4j + PostgreSQL)
    # ------------------------------------------------------------------
    def compare_localities(
        self,
        *,
        locality_id_a: str,
        locality_id_b: str,
        interaction_type_code: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> LocalityComparisonResultDTO:
        if locality_id_a == locality_id_b:
            raise DomainValidationError("Las localidades a comparar deben ser distintas.")

        loc_a = self._relational.get_locality_by_id(locality_id_a)
        if loc_a is None:
            raise EntityNotFoundError("Locality", locality_id_a)
        loc_b = self._relational.get_locality_by_id(locality_id_b)
        if loc_b is None:
            raise EntityNotFoundError("Locality", locality_id_b)

        it_code = _validate_interaction_filter(interaction_type_code)
        p_start, p_end = _validate_period_filters(period_start, period_end)

        edges_a = self._graph.get_locality_assertions(
            locality_id=locality_id_a,
            interaction_type_code=it_code,
            period_start=p_start,
            period_end=p_end,
        )
        edges_b = self._graph.get_locality_assertions(
            locality_id=locality_id_b,
            interaction_type_code=it_code,
            period_start=p_start,
            period_end=p_end,
        )

        # Validar en PostgreSQL que todas las afirmaciones continuan PUBLISHED
        all_aids = [e.assertion_id for e in edges_a] + [e.assertion_id for e in edges_b]
        published_map = self._relational.get_published_assertions_by_ids(all_aids)
        valid_a = [published_map[e.assertion_id] for e in edges_a if e.assertion_id in published_map]
        valid_b = [published_map[e.assertion_id] for e in edges_b if e.assertion_id in published_map]

        sources_by_aid = {e.assertion_id: e.source_ids for e in (*edges_a, *edges_b)}

        def _compute_metrics(loc: LocalitySummaryDTO, items: list[InteractionListItemDTO]) -> tuple[LocalityNetworkMetricsDTO, set[str], dict[str, TaxonSummaryDTO], set[tuple[str, str, str]]]:
            taxa_map: dict[str, TaxonSummaryDTO] = {}
            animals: set[str] = set()
            plants: set[str] = set()
            sources: set[str] = set()
            pairs: set[tuple[str, str, str]] = set()
            for item in items:
                taxa_map[item.actor_taxon.taxon_id] = item.actor_taxon
                taxa_map[item.target_taxon.taxon_id] = item.target_taxon
                if item.actor_taxon.organism_group == OrganismGroup.ANIMAL.value:
                    animals.add(item.actor_taxon.taxon_id)
                if item.target_taxon.organism_group == OrganismGroup.PLANT.value:
                    plants.add(item.target_taxon.taxon_id)
                sources.update(sources_by_aid.get(item.assertion_id, []))
                pairs.add((item.actor_taxon.taxon_id, item.target_taxon.taxon_id, item.interaction_type_code))
            metrics = LocalityNetworkMetricsDTO(
                locality=loc,
                published_assertions_count=len(items),
                distinct_taxa_count=len(taxa_map),
                distinct_animal_taxa_count=len(animals),
                distinct_plant_taxa_count=len(plants),
                source_ids=sorted(sources),
            )
            return metrics, set(taxa_map.keys()), taxa_map, pairs

        metrics_a, ids_a, taxa_map_a, pairs_a = _compute_metrics(loc_a, valid_a)
        metrics_b, ids_b, taxa_map_b, pairs_b = _compute_metrics(loc_b, valid_b)

        combined_taxa = {**taxa_map_a, **taxa_map_b}
        shared_ids = sorted(ids_a & ids_b)
        only_a_ids = sorted(ids_a - ids_b)
        only_b_ids = sorted(ids_b - ids_a)

        shared_pairs = sorted(pairs_a & pairs_b)
        shared_pair_dtos = [
            SharedInteractionPairDTO(
                actor_taxon_id=act_id,
                actor_name=combined_taxa[act_id].accepted_name,
                target_taxon_id=tgt_id,
                target_name=combined_taxa[tgt_id].accepted_name,
                interaction_type_code=code,
            )
            for act_id, tgt_id, code in shared_pairs
        ]

        undetermined_count = self._relational.count_undetermined_published_assertions(
            period_start=p_start,
            period_end=p_end,
            interaction_type_code=it_code,
        )

        applied_filters = {
            k: v
            for k, v in {
                "locality_id_a": locality_id_a,
                "locality_id_b": locality_id_b,
                "interaction_type_code": it_code,
                "period_start": p_start,
                "period_end": p_end,
            }.items()
            if v is not None
        }

        return LocalityComparisonResultDTO(
            locality_a=metrics_a,
            locality_b=metrics_b,
            shared_taxa=[combined_taxa[tid] for tid in shared_ids],
            exclusive_to_a_taxa=[combined_taxa[tid] for tid in only_a_ids],
            exclusive_to_b_taxa=[combined_taxa[tid] for tid in only_b_ids],
            shared_interaction_pairs=shared_pair_dtos,
            undetermined_locality_assertions_count=undetermined_count,
            applied_filters=applied_filters,
            warnings=[
                DISCLAIMER_LOCALITY_SEPARATION,
                DISCLAIMER_CONNECTIVITY_NOT_ABUNDANCE,
                DISCLAIMER_FRUGIVORY_NO_DISPERSAL,
            ],
        )

    # ------------------------------------------------------------------
    # OP-07: Calcular conectividad descriptiva (Neo4j + PostgreSQL)
    # ------------------------------------------------------------------
    def calculate_descriptive_connectivity(
        self,
        *,
        taxon_id: str,
        locality_id: str | None = None,
        interaction_type_code: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> ConnectivityResultDTO:
        taxon = self._relational.get_taxon_by_id(taxon_id)
        if taxon is None:
            raise EntityNotFoundError("Taxon", taxon_id)

        if locality_id and self._relational.get_locality_by_id(locality_id) is None:
            raise EntityNotFoundError("Locality", locality_id)

        it_code = _validate_interaction_filter(interaction_type_code)
        p_start, p_end = _validate_period_filters(period_start, period_end)

        edges = self._graph.get_taxon_connectivity_edges(
            taxon_id=taxon_id,
            locality_id=locality_id,
            interaction_type_code=it_code,
            period_start=p_start,
            period_end=p_end,
        )

        # Confirmar contra PostgreSQL que las afirmaciones siguen PUBLISHED
        published_map = self._relational.get_published_assertions_by_ids([e.assertion_id for e in edges])
        valid_edges: list[GraphAssertionEdgeDTO] = [e for e in edges if e.assertion_id in published_map]

        partner_assertions: dict[str, int] = defaultdict(int)
        partner_roles: dict[str, set[str]] = defaultdict(set)
        partner_types: dict[str, set[str]] = defaultdict(set)
        partner_locs: dict[str, set[str]] = defaultdict(set)
        as_actor_partners: set[str] = set()
        as_target_partners: set[str] = set()

        for e in valid_edges:
            if e.actor_taxon_id == taxon_id:
                partner_id = e.target_taxon_id
                partner_roles[partner_id].add("target")
                as_actor_partners.add(partner_id)
            else:
                partner_id = e.actor_taxon_id
                partner_roles[partner_id].add("actor")
                as_target_partners.add(partner_id)
            partner_assertions[partner_id] += 1
            partner_types[partner_id].add(e.interaction_type_code)
            partner_locs[partner_id].add(e.locality_id)

        partner_ids = sorted(partner_assertions.keys())
        partner_taxa = self._relational.get_taxa_by_ids(partner_ids)
        partners: list[PartnerConnectivityDTO] = []
        for pid in partner_ids:
            p_taxon = partner_taxa.get(pid)
            if p_taxon is None:
                continue
            roles = partner_roles[pid]
            role_label = "both" if len(roles) > 1 else next(iter(roles))
            partners.append(
                PartnerConnectivityDTO(
                    taxon=p_taxon,
                    role_of_partner=role_label,
                    assertions_count=partner_assertions[pid],
                    interaction_type_codes=sorted(partner_types[pid]),
                    locality_ids=sorted(partner_locs[pid]),
                )
            )

        applied_filters = {
            k: v
            for k, v in {
                "taxon_id": taxon_id,
                "locality_id": locality_id,
                "interaction_type_code": it_code,
                "period_start": p_start,
                "period_end": p_end,
            }.items()
            if v is not None
        }

        return ConnectivityResultDTO(
            taxon=taxon,
            distinct_partner_taxa_count=len(partners),
            documented_assertions_count=len(valid_edges),
            as_actor_distinct_partners=len(as_actor_partners),
            as_target_distinct_partners=len(as_target_partners),
            partners=partners,
            applied_filters=applied_filters,
            warnings=[DISCLAIMER_CONNECTIVITY_NOT_ABUNDANCE, DISCLAIMER_FRUGIVORY_NO_DISPERSAL],
        )

    # ------------------------------------------------------------------
    # OP-08: Consultar fuente o estudio (PostgreSQL + MongoDB)
    # ------------------------------------------------------------------
    def get_source_detail(self, source_id: str) -> SourceDetailResultDTO:
        source = self._relational.get_source_by_id(source_id)
        if source is None:
            raise EntityNotFoundError("Source", source_id)

        versions = self._relational.get_source_versions(source_id)
        published_count, evidences_count = self._relational.get_source_usage_counts(source_id)
        # Propaga DatabaseUnavailableError (503) si MongoDB no responde
        study_dossier = self._document.get_study_dossier(source_id)

        warnings: list[str] = []
        if study_dossier is None:
            warnings.append(
                "No existe expediente metodologico documental (study_dossier) para esta fuente."
            )
        if not versions:
            warnings.append(
                "La fuente representa una referencia bibliografica sin versiones de dataset descargado registradas."
            )

        return SourceDetailResultDTO(
            source=source,
            versions=versions,
            published_assertions_count=published_count,
            evidences_count=evidences_count,
            has_study_dossier=study_dossier is not None,
            study_dossier=study_dossier,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # OP-15: Consultar ocurrencias (PostgreSQL + referencia documental opcional)
    # ------------------------------------------------------------------
    def list_occurrences(
        self,
        *,
        taxon_id: str | None = None,
        locality_id: str | None = None,
        source_id: str | None = None,
        georeference_status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        include_raw_reference: bool = False,
        sort_by: str = "occurrence_id",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult[OccurrenceItemDTO]:
        _validate_pagination(page, page_size)
        if taxon_id and self._relational.get_taxon_by_id(taxon_id) is None:
            raise EntityNotFoundError("Taxon", taxon_id)
        if locality_id and self._relational.get_locality_by_id(locality_id) is None:
            raise EntityNotFoundError("Locality", locality_id)
        if source_id and self._relational.get_source_by_id(source_id) is None:
            raise EntityNotFoundError("Source", source_id)

        allowed_georef = {"GEOREFERENCED", "APPROXIMATE", "UNVERIFIED", "NOT_GEOREFERENCED"}
        if georeference_status and georeference_status not in allowed_georef:
            raise DomainValidationError(f"Estado de georreferenciacion invalido: '{georeference_status}'.")

        d_from, d_to = _validate_period_filters(date_from, date_to)

        result = self._relational.list_occurrences(
            taxon_id=taxon_id,
            locality_id=locality_id,
            source_id=source_id,
            georeference_status=georeference_status,
            date_from=d_from,
            date_to=d_to,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            page_size=page_size,
        )
        if not include_raw_reference or not result.items:
            return result

        enriched_items: list[OccurrenceItemDTO] = []
        for item in result.items:
            raw_ref = None
            if item.source_record_id:
                raw_ref = self._document.get_raw_source_record_by_external_id(
                    item.source.source_id,
                    item.source_record_id,
                )
            enriched_items.append(
                OccurrenceItemDTO(
                    occurrence_id=item.occurrence_id,
                    taxon=item.taxon,
                    source=item.source,
                    locality=item.locality,
                    source_record_id=item.source_record_id,
                    event_date=item.event_date,
                    latitude=item.latitude,
                    longitude=item.longitude,
                    coordinate_uncertainty_meters=item.coordinate_uncertainty_meters,
                    basis_of_record=item.basis_of_record,
                    georeference_status=item.georeference_status,
                    is_interaction_evidence=False,
                    raw_source_reference=raw_ref,
                )
            )
        return PaginatedResult(
            items=enriched_items,
            total=result.total,
            page=result.page,
            page_size=result.page_size,
        )
