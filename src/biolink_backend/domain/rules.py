from __future__ import annotations

from collections.abc import Sequence

from biolink_backend.domain.entities import Evidence, InteractionAssertion, Taxon
from biolink_backend.domain.vocabularies import AssertionStatus, InteractionTypeCode, OrganismGroup
from biolink_backend.shared.exceptions import BusinessRuleViolationError

V1_ALLOWED_INTERACTION_TYPES: frozenset[InteractionTypeCode] = frozenset({
    InteractionTypeCode.CONSUMES_FRUIT_OF,
    InteractionTypeCode.REMOVES_FRUIT_OF,
    InteractionTypeCode.CONSUMES_OR_REMOVES_FRUIT_OF,
})

ALLOWED_TRANSITIONS: dict[AssertionStatus, frozenset[AssertionStatus]] = {
    AssertionStatus.DRAFT: frozenset({AssertionStatus.IN_REVIEW}),
    AssertionStatus.IN_REVIEW: frozenset({AssertionStatus.PUBLISHED, AssertionStatus.REJECTED}),
    AssertionStatus.PUBLISHED: frozenset({AssertionStatus.WITHDRAWN}),
    AssertionStatus.REJECTED: frozenset({AssertionStatus.IN_REVIEW}),
    AssertionStatus.WITHDRAWN: frozenset({AssertionStatus.IN_REVIEW}),
}


def validate_v1_actor_target_groups(actor: Taxon, target: Taxon) -> None:
    """Valida RD-03: actor != objetivo y en V1 actor animal -> objetivo vegetal."""
    if actor.taxon_id == target.taxon_id:
        raise BusinessRuleViolationError(
            "RD-03",
            "El taxon actor y el taxon objetivo deben ser distintos.",
            details={"taxon_id": actor.taxon_id},
        )
    if actor.organism_group != OrganismGroup.ANIMAL:
        raise BusinessRuleViolationError(
            "RD-03",
            f"En V1 el taxon actor debe pertenecer al grupo ANIMAL (recibido: {actor.organism_group}).",
            details={"actor_taxon_id": actor.taxon_id, "organism_group": actor.organism_group},
        )
    if target.organism_group != OrganismGroup.PLANT:
        raise BusinessRuleViolationError(
            "RD-03",
            f"En V1 el taxon objetivo debe pertenecer al grupo PLANT (recibido: {target.organism_group}).",
            details={"target_taxon_id": target.taxon_id, "organism_group": target.organism_group},
        )


def validate_interaction_type_for_v1(code: InteractionTypeCode) -> None:
    """Valida RD-04 y alcance V1 (DISPERSES_SEEDS_OF no se afirma por defecto en V1)."""
    if code not in V1_ALLOWED_INTERACTION_TYPES:
        raise BusinessRuleViolationError(
            "RD-05",
            f"El tipo de interaccion '{code}' no esta habilitado como afirmacion directa en V1.",
            details={"interaction_type_code": code},
        )


def validate_state_transition(current_status: AssertionStatus, new_status: AssertionStatus) -> None:
    """Valida transiciones permitidas del ciclo de vida de afirmaciones."""
    if current_status == new_status:
        raise BusinessRuleViolationError(
            "RD-13",
            f"La afirmacion ya se encuentra en estado '{current_status}'.",
        )
    allowed = ALLOWED_TRANSITIONS.get(current_status, frozenset())
    if new_status not in allowed:
        raise BusinessRuleViolationError(
            "RD-13",
            f"Transicion de estado no permitida: '{current_status}' -> '{new_status}'.",
            details={"current_status": current_status, "new_status": new_status},
        )


def validate_publication_requirements(
    assertion: InteractionAssertion,
    evidences: Sequence[Evidence],
    reviewed_by: str | None,
) -> None:
    """Valida RD-02 y RD-13: una afirmacion PUBLISHED requiere >= 1 evidencia y revisor."""
    if not evidences:
        raise BusinessRuleViolationError(
            "RD-02",
            f"La afirmacion '{assertion.assertion_id}' requiere al menos una evidencia para publicarse.",
            details={"assertion_id": assertion.assertion_id},
        )
    if not reviewed_by or not reviewed_by.strip():
        raise BusinessRuleViolationError(
            "RD-13",
            f"La afirmacion '{assertion.assertion_id}' requiere identificador de revisor al publicar.",
            details={"assertion_id": assertion.assertion_id},
        )


def ensure_not_occurrence_only(has_explicit_interaction_evidence: bool) -> None:
    """Valida RD-01 y RD-06: la mera presencia u ocurrencia no crea interaccion."""
    if not has_explicit_interaction_evidence:
        raise BusinessRuleViolationError(
            "RD-01",
            "Un registro de ocurrencia o co-presencia no constituye evidencia de interaccion ecologica.",
        )


def should_project_to_graph(status: AssertionStatus) -> bool:
    """Valida RD-16: solo las afirmaciones PUBLISHED se proyectan al grafo Neo4j."""
    return status == AssertionStatus.PUBLISHED
