from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from biolink_backend.application.dto import (
    EvidenceDetailDTO,
    GraphAssertionEdgeDTO,
    InteractionListItemDTO,
    LocalitySummaryDTO,
    OccurrenceItemDTO,
    PaginatedResult,
    RawSourceReferenceDTO,
    SourceSummaryDTO,
    SourceVersionDTO,
    StudyDossierDTO,
    TaxonDossierClaimDTO,
    TaxonDossierDTO,
    TaxonIdentifierDTO,
    TaxonNameDTO,
    TaxonSummaryDTO,
)
from biolink_backend.application.services.public_query_service import PublicQueryService
from biolink_backend.shared.exceptions import (
    BusinessRuleViolationError,
    DatabaseUnavailableError,
    DomainValidationError,
    EntityNotFoundError,
)


def _sample_fixtures() -> dict[str, Any]:
    bat = TaxonSummaryDTO("TAX_AN_001", "Artibeus lituratus", "(Olfers, 1818)", "SPECIES", "ANIMAL", "ACCEPTED")
    sturnira = TaxonSummaryDTO("TAX_AN_003", "Sturnira lilium", "(E. Geoffroy, 1810)", "SPECIES", "ANIMAL", "ACCEPTED")
    cecropia = TaxonSummaryDTO("TAX_PL_001", "Cecropia obtusifolia", "", "SPECIES", "PLANT", "ACCEPTED")
    ficus_sp = TaxonSummaryDTO("TAX_PL_004", "Ficus sp.", "", "GENUS", "PLANT", "UNRESOLVED_TO_SPECIES")

    loc_rbma = LocalitySummaryDTO("LOC_RBMA_SOUTH", "Montes Azules (sector sur)", "STUDY_AREA", "PROTECTED_ANP", "INSIDE_ANP", "REGIONAL_STUDY_SECTOR", None)
    loc_pdlg = LocalitySummaryDTO("LOC_PDLG", "Ejido Playon de la Gloria", "EJIDO", "EXTERNAL_EJIDO", "OUTSIDE_ANP", "EJIDO_LOCALITY", None)
    loc_undet = LocalitySummaryDTO("LOC_LACANDONA_UNDETERMINED", "Selva Lacandona (indeterminada)", "STUDY_AREA", "UNDETERMINED", "PENDING_VALIDATION", "REGIONAL_UNSPECIFIED", None)

    src_bat = SourceSummaryDTO("BAT2007", "PUBLICATION", "Diversidad de frutos...", "Olea-Wagner et al.", 2007, "Olea-Wagner et al. (2007)", "ACADEMIC_CITATION_ONLY", "10.22201/ib.20078706e.2007.001.393")

    a1 = InteractionListItemDTO(
        "AST_SCI_001", "PUBLISHED", "CONSUMES_FRUIT_OF", bat, cecropia, loc_rbma,
        "2004-01", "2004-07", "Dry & rainy 2004", "FECAL_SEED_IDENTIFICATION", "REFERENCE_FIXTURE",
        [src_bat.citation_text], ["pp. 195-197"], ["Does not demonstrate seedling establishment"],
    )
    a2 = InteractionListItemDTO(
        "AST_SCI_002", "PUBLISHED", "CONSUMES_FRUIT_OF", bat, cecropia, loc_pdlg,
        "2004-01", "2004-07", "Dry & rainy 2004", "FECAL_SEED_IDENTIFICATION", "REFERENCE_FIXTURE",
        [src_bat.citation_text], ["pp. 195-197"], ["External to Montes Azules ANP"],
    )
    e1 = EvidenceDetailDTO(
        "EVD_AST_SCI_001", "BAT2007", "SV_BAT2007_V1", "pp. 195-197", "OBSERVATION",
        "FECAL_SEED_IDENTIFICATION", "CONSUMES_FRUIT_OF", "High consumption frequency",
        "Does not demonstrate seedling establishment", "2004-01", "2004-07", "ACADEMIC_CITATION_ONLY",
        "PRIMARY", None, src_bat,
    )
    occ1 = OccurrenceItemDTO(
        "OCC_SCI_001", bat, src_bat, loc_rbma, "BAT2007_PRES_RBMA_01", "2004-01",
        None, None, None, "HUMAN_OBSERVATION", "NOT_GEOREFERENCED", False,
    )
    return {
        "taxa": {t.taxon_id: t for t in (bat, sturnira, cecropia, ficus_sp)},
        "locs": {l.locality_id: l for l in (loc_rbma, loc_pdlg, loc_undet)},
        "sources": {"BAT2007": src_bat},
        "assertions": {"AST_SCI_001": a1, "AST_SCI_002": a2},
        "evidences": {"AST_SCI_001": [e1], "AST_SCI_002": [e1]},
        "occurrences": [occ1],
    }


@dataclass
class FakeRelationalPort:
    fx: dict[str, Any]
    fail_with: Exception | None = None

    def search_taxa(self, **kwargs) -> PaginatedResult[TaxonSummaryDTO]:
        if self.fail_with:
            raise self.fail_with
        q = (kwargs.get("query") or "").lower()
        items = list(self.fx["taxa"].values())
        if q:
            if "parvidens" in q:
                st = self.fx["taxa"]["TAX_AN_003"]
                items = [TaxonSummaryDTO(st.taxon_id, st.accepted_name, st.scientific_authorship, st.taxonomic_rank, st.organism_group, st.taxonomic_status, matched_name="Sturnira parvidens", matched_usage="SYNONYM", matched_verbatim_name="Sturnira lilium")]
            else:
                items = [t for t in items if q in t.accepted_name.lower()]
        return PaginatedResult(items=items, total=len(items), page=kwargs["page"], page_size=kwargs["page_size"])

    def get_taxon_by_id(self, taxon_id: str) -> TaxonSummaryDTO | None:
        return self.fx["taxa"].get(taxon_id)

    def get_taxa_by_ids(self, taxon_ids: list[str]) -> dict[str, TaxonSummaryDTO]:
        return {k: v for k, v in self.fx["taxa"].items() if k in taxon_ids}

    def get_taxon_names(self, taxon_id: str) -> list[TaxonNameDTO]:
        return [TaxonNameDTO(f"NM_{taxon_id}", self.fx["taxa"][taxon_id].accepted_name, "ACCEPTED", "la", "BAT2007", True, None)]

    def get_taxon_identifiers(self, taxon_id: str) -> list[TaxonIdentifierDTO]:
        return [TaxonIdentifierDTO(f"TID_{taxon_id}", "CUSTOM", f"REF:BAT2007:{taxon_id}", "BAT2007")]

    def get_locality_by_id(self, locality_id: str) -> LocalitySummaryDTO | None:
        return self.fx["locs"].get(locality_id)

    def get_localities_by_ids(self, locality_ids: list[str]) -> dict[str, LocalitySummaryDTO]:
        return {k: v for k, v in self.fx["locs"].items() if k in locality_ids}

    def get_published_assertion_by_id(self, assertion_id: str) -> InteractionListItemDTO | None:
        return self.fx["assertions"].get(assertion_id)

    def get_published_assertions_by_ids(self, assertion_ids: list[str]) -> dict[str, InteractionListItemDTO]:
        return {k: v for k, v in self.fx["assertions"].items() if k in assertion_ids}

    def get_evidences_for_assertion(self, assertion_id: str) -> list[EvidenceDetailDTO]:
        return self.fx["evidences"].get(assertion_id, [])

    def count_undetermined_published_assertions(self, **_kw) -> int:
        return 1

    def get_source_by_id(self, source_id: str) -> SourceSummaryDTO | None:
        return self.fx["sources"].get(source_id)

    def get_source_versions(self, source_id: str) -> list[SourceVersionDTO]:
        return [SourceVersionDTO("SV_BAT2007_V1", "stage1", "2026-11-20", "2026-11-20T00:00:00+00:00", "csv", {}, {}, "ACADEMIC_CITATION_ONLY", "a" * 64)]

    def get_source_usage_counts(self, source_id: str) -> tuple[int, int]:
        return 2, 2

    def list_occurrences(self, **kwargs) -> PaginatedResult[OccurrenceItemDTO]:
        return PaginatedResult(items=self.fx["occurrences"], total=len(self.fx["occurrences"]), page=kwargs["page"], page_size=kwargs["page_size"])


@dataclass
class FakeDocumentPort:
    fail_with: Exception | None = None

    def get_taxon_dossier(self, taxon_id: str) -> TaxonDossierDTO | None:
        if self.fail_with:
            raise self.fail_with
        if taxon_id == "TAX_AN_001":
            return TaxonDossierDTO(
                "TAX_AN_001", 1, 2, 1, "Fruit bat", ["BAT2007"], ["p. 195"], {"ecology": {}},
                [TaxonDossierClaimDTO("C1", "ecology.diet", "Cecropia", "BAT2007", "Table 2")], [], "2026-11-21T00:00:00Z"
            )
        return None

    def get_study_dossier(self, source_id: str) -> StudyDossierDTO | None:
        if self.fail_with:
            raise self.fail_with
        if source_id == "BAT2007":
            return StudyDossierDTO("STD_BAT2007", "BAT2007", 1, "10.22201/...", "Frugivory", {"methods": ["FECAL"]}, ["LOC_RBMA_SOUTH", "LOC_PDLG"], {}, ["TAX_AN_001"], ["No germination proved"], {"license_id": "ACADEMIC"}, "a" * 64)
        return None

    def get_study_dossiers_by_source_ids(self, source_ids: list[str]) -> list[StudyDossierDTO]:
        res = [self.get_study_dossier(s) for s in source_ids]
        return [r for r in res if r is not None]

    def get_raw_source_record_by_external_id(self, source_id: str, external_record_id: str) -> RawSourceReferenceDTO | None:
        return RawSourceReferenceDTO(f"RAW_{external_record_id}", "BAT_1", source_id, external_record_id, source_id, "b" * 64, "ACADEMIC_CITATION_ONLY", False, None)


@dataclass
class FakeGraphPort:
    fail_with: Exception | None = None
    include_draft_in_graph: bool = False

    def _edges(self) -> list[GraphAssertionEdgeDTO]:
        items = [
            GraphAssertionEdgeDTO("AST_SCI_001", "TAX_AN_001", "TAX_PL_001", "CONSUMES_FRUIT_OF", "LOC_RBMA_SOUTH", "2004-01", "2004-07", "2004", "FECAL_SEED_IDENTIFICATION", ["BAT2007"]),
            GraphAssertionEdgeDTO("AST_SCI_002", "TAX_AN_001", "TAX_PL_001", "CONSUMES_FRUIT_OF", "LOC_PDLG", "2004-01", "2004-07", "2004", "FECAL_SEED_IDENTIFICATION", ["BAT2007"]),
        ]
        if self.include_draft_in_graph:
            items.append(GraphAssertionEdgeDTO("AST_DRAFT", "TAX_AN_001", "TAX_PL_004", "CONSUMES_FRUIT_OF", "LOC_RBMA_SOUTH", "2004-01", "2004-07", "2004", "FECAL_SEED_IDENTIFICATION", ["BAT2007"]))
        return items

    def list_taxon_interactions(self, **kwargs) -> tuple[list[GraphAssertionEdgeDTO], int]:
        if self.fail_with:
            raise self.fail_with
        edges = self._edges()
        return edges, len(edges)

    def is_assertion_projected(self, assertion_id: str) -> bool:
        if self.fail_with:
            raise self.fail_with
        return assertion_id in ("AST_SCI_001", "AST_SCI_002")

    def get_filtered_subgraph(self, **kwargs) -> list[GraphAssertionEdgeDTO]:
        if self.fail_with:
            raise self.fail_with
        edges = self._edges()
        loc = kwargs.get("locality_id")
        if loc:
            edges = [e for e in edges if e.locality_id == loc]
        return edges

    def get_locality_assertions(self, **kwargs) -> list[GraphAssertionEdgeDTO]:
        if self.fail_with:
            raise self.fail_with
        return [e for e in self._edges() if e.locality_id == kwargs["locality_id"]]

    def get_taxon_connectivity_edges(self, **kwargs) -> list[GraphAssertionEdgeDTO]:
        if self.fail_with:
            raise self.fail_with
        return self._edges()


def test_op01_search_synonym_and_validation() -> None:
    fx = _sample_fixtures()
    svc = PublicQueryService(relational=FakeRelationalPort(fx), document=FakeDocumentPort(), graph=FakeGraphPort())

    res = svc.search_taxa(query="Sturnira parvidens")
    assert res.total == 1
    assert res.items[0].taxon_id == "TAX_AN_003"
    assert res.items[0].matched_name == "Sturnira parvidens"
    assert res.items[0].matched_usage == "SYNONYM"

    with pytest.raises(DomainValidationError):
        svc.search_taxa(taxonomic_rank="INVALID_RANK")


def test_op02_fiche_preserves_genus_rank_and_propagates_mongo_failure() -> None:
    fx = _sample_fixtures()
    svc = PublicQueryService(relational=FakeRelationalPort(fx), document=FakeDocumentPort(), graph=FakeGraphPort())

    detail = svc.get_taxon_fiche("TAX_AN_001")
    assert detail.has_dossier is True
    assert detail.dossier is not None and detail.dossier.history_versions_count == 1

    # Taxon a nivel GENUS conserva su rango (RD-10) y advierte si no hay dossier
    genus_detail = svc.get_taxon_fiche("TAX_PL_004")
    assert genus_detail.has_dossier is False
    assert any("GENUS" in w for w in genus_detail.warnings)

    # Si MongoDB cae, no devolver exito parcial ocultando la falla
    failing_svc = PublicQueryService(
        relational=FakeRelationalPort(fx),
        document=FakeDocumentPort(fail_with=DatabaseUnavailableError("mongodb", "down")),
        graph=FakeGraphPort(),
    )
    with pytest.raises(DatabaseUnavailableError):
        failing_svc.get_taxon_fiche("TAX_AN_001")


def test_op03_and_op04_exclude_drafts_and_protect_licenses() -> None:
    fx = _sample_fixtures()
    svc = PublicQueryService(
        relational=FakeRelationalPort(fx),
        document=FakeDocumentPort(),
        graph=FakeGraphPort(include_draft_in_graph=True),
    )

    # Aunque existiera una proyeccion residual DRAFT en el grafo, el filtro contra Postgres la excluye
    interactions = svc.list_taxon_interactions(taxon_id="TAX_AN_001")
    assert all(item.status == "PUBLISHED" for item in interactions.items)
    assert [item.assertion_id for item in interactions.items] == ["AST_SCI_001", "AST_SCI_002"]

    # OP-04 rechaza afirmacion no publicada o inexistente con 404
    with pytest.raises(EntityNotFoundError):
        svc.get_interaction_detail("AST_SCI_DRAFT_001")

    # OP-04 no expone raw_payload si la licencia prohibe redistribucion (RNF-08)
    detail = svc.get_interaction_detail("AST_SCI_001")
    assert detail.graph_projected is True
    assert len(detail.raw_source_references) == 1
    assert detail.raw_source_references[0].redistribution_permitted is False
    assert detail.raw_source_references[0].raw_payload is None


def test_op05_op06_op07_scientific_rules() -> None:
    fx = _sample_fixtures()
    svc = PublicQueryService(relational=FakeRelationalPort(fx), document=FakeDocumentPort(), graph=FakeGraphPort())

    # OP-05 excluye localidades indeterminadas por defecto y reporta conteo separado
    sub = svc.filter_documented_subgraph(max_depth=1)
    assert sub.undetermined_locality_excluded_count == 1
    assert any("Montes Azules" in w for w in sub.warnings)

    with pytest.raises(DomainValidationError):
        svc.filter_documented_subgraph(max_depth=5)

    # OP-06 rechaza comparar misma localidad y mantiene Montes Azules y PDLG separados
    with pytest.raises(DomainValidationError):
        svc.compare_localities(locality_id_a="LOC_RBMA_SOUTH", locality_id_b="LOC_RBMA_SOUTH")

    cmp_res = svc.compare_localities(locality_id_a="LOC_RBMA_SOUTH", locality_id_b="LOC_PDLG")
    assert cmp_res.locality_a.locality.protected_status == "PROTECTED_ANP"
    assert cmp_res.locality_b.locality.protected_status == "EXTERNAL_EJIDO"
    assert cmp_res.undetermined_locality_assertions_count == 1
    assert len(cmp_res.shared_taxa) == 2

    # OP-07 diferencia socios distintos (1: TAX_PL_001) del numero de afirmaciones (2: RBMA + PDLG)
    conn_res = svc.calculate_descriptive_connectivity(taxon_id="TAX_AN_001")
    assert conn_res.distinct_partner_taxa_count == 1
    assert conn_res.documented_assertions_count == 2
    assert any("abundancia" in w.lower() for w in conn_res.warnings)


def test_op08_and_op15_source_and_occurrences() -> None:
    fx = _sample_fixtures()
    svc = PublicQueryService(relational=FakeRelationalPort(fx), document=FakeDocumentPort(), graph=FakeGraphPort())

    src = svc.get_source_detail("BAT2007")
    assert src.has_study_dossier is True
    assert len(src.versions) == 1

    occ = svc.list_occurrences(taxon_id="TAX_AN_001")
    assert occ.total == 1
    assert occ.items[0].is_interaction_evidence is False
