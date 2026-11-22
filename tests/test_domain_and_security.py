from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from biolink_backend.api.dependencies import get_auth_service, require_roles
from biolink_backend.application.ports import StoredSessionRecord, StoredUserRecord
from biolink_backend.application.services.auth_service import AuthService
from biolink_backend.config.settings import Settings
from biolink_backend.domain.entities import Evidence, InteractionAssertion, Taxon
from biolink_backend.domain.rules import (
    ensure_not_occurrence_only,
    should_project_to_graph,
    validate_interaction_type_for_v1,
    validate_publication_requirements,
    validate_state_transition,
    validate_v1_actor_target_groups,
)
from biolink_backend.domain.value_objects import ChecksumSHA256, PartialDateRange
from biolink_backend.domain.vocabularies import (
    AssertionStatus,
    EvidenceKind,
    EvidenceMethodCode,
    InteractionTypeCode,
    OrganismGroup,
    RoleCode,
    TaxonomicRank,
    TaxonomicStatus,
)
from biolink_backend.main import create_app
from biolink_backend.security.passwords import hash_password, verify_password
from biolink_backend.security.rbac import AuthenticatedUser, ensure_has_any_role
from biolink_backend.security.tokens import generate_session_token, hash_session_token
from biolink_backend.shared.exceptions import AuthorizationError, BusinessRuleViolationError, DomainValidationError
from biolink_backend.shared.time import utc_now
from tests.conftest import build_fake_health_service


def test_partial_date_range_and_checksum_validation() -> None:
    r = PartialDateRange("2004-01", "2004-07")
    assert r.period_start == "2004-01"
    with pytest.raises(DomainValidationError):
        PartialDateRange("2004-07", "2004-01")
    with pytest.raises(DomainValidationError):
        PartialDateRange("invalid-date", "2004-07")

    digest = ChecksumSHA256("A" * 64)
    assert digest.value == "a" * 64
    with pytest.raises(DomainValidationError):
        ChecksumSHA256("short")


def test_domain_rules_v1_enforcement() -> None:
    bat = Taxon("TX_AN_001", "Artibeus lituratus", "(Olfers, 1818)", TaxonomicRank.SPECIES, OrganismGroup.ANIMAL, TaxonomicStatus.ACCEPTED)
    fig = Taxon("TX_PL_001", "Ficus cotinifolia", "Kunth", TaxonomicRank.SPECIES, OrganismGroup.PLANT, TaxonomicStatus.ACCEPTED)

    validate_v1_actor_target_groups(bat, fig)
    with pytest.raises(BusinessRuleViolationError):
        validate_v1_actor_target_groups(bat, bat)
    with pytest.raises(BusinessRuleViolationError):
        validate_v1_actor_target_groups(fig, bat)

    validate_interaction_type_for_v1(InteractionTypeCode.CONSUMES_OR_REMOVES_FRUIT_OF)
    with pytest.raises(BusinessRuleViolationError):
        validate_interaction_type_for_v1(InteractionTypeCode.DISPERSES_SEEDS_OF)

    with pytest.raises(BusinessRuleViolationError):
        ensure_not_occurrence_only(False)

    validate_state_transition(AssertionStatus.DRAFT, AssertionStatus.IN_REVIEW)
    validate_state_transition(AssertionStatus.IN_REVIEW, AssertionStatus.PUBLISHED)
    with pytest.raises(BusinessRuleViolationError):
        validate_state_transition(AssertionStatus.DRAFT, AssertionStatus.PUBLISHED)

    assert should_project_to_graph(AssertionStatus.PUBLISHED) is True
    assert should_project_to_graph(AssertionStatus.DRAFT) is False
    assert should_project_to_graph(AssertionStatus.WITHDRAWN) is False

    assertion = InteractionAssertion(
        assertion_id="AST_001",
        actor_taxon_id=bat.taxon_id,
        target_taxon_id=fig.taxon_id,
        interaction_type_code=InteractionTypeCode.CONSUMES_FRUIT_OF,
        locality_id="LOC_RBMA_SOUTH",
        period=PartialDateRange("2004-01", "2004-07"),
        season_label="2004",
        method_code=EvidenceMethodCode.FECAL_SEED_IDENTIFICATION,
        assertion_scope="V1",
        status=AssertionStatus.IN_REVIEW,
        created_by="USR_1",
        created_at=datetime.now(timezone.utc),
    )
    ev = Evidence(
        evidence_id="EVD_001",
        source_id="BAT2007",
        source_locator="p. 195",
        evidence_kind=EvidenceKind.OBSERVATION,
        method_code=EvidenceMethodCode.FECAL_SEED_IDENTIFICATION,
        reported_term="frugivory",
        summary="Seeds in feces",
        interpretation_limit="Does not prove establishment",
        period=PartialDateRange("2004-01", "2004-07"),
        license_id="ACADEMIC",
    )
    with pytest.raises(BusinessRuleViolationError):
        validate_publication_requirements(assertion, [], "USR_REV")
    validate_publication_requirements(assertion, [ev], "USR_REV")


def test_password_hashing_and_session_tokens() -> None:
    pwd = "StrongBioLinkPassword!2026"
    h1 = hash_password(pwd, iterations=10_000)
    h2 = hash_password(pwd, iterations=10_000)
    assert h1 != pwd
    assert h1 != h2
    assert verify_password(pwd, h1) is True
    assert verify_password("WrongPassword", h1) is False

    raw_tok = generate_session_token()
    tok_hash = hash_session_token(raw_tok)
    assert tok_hash != raw_tok
    assert len(tok_hash) == 64


class InMemoryAuthRepo:
    def __init__(self, password_hash: str) -> None:
        self.user = StoredUserRecord(
            user_id="USR_CUR_1",
            display_name="Curador Prueba",
            email="curador@biolink.org",
            password_hash=password_hash,
            is_active=True,
            roles=frozenset({RoleCode.CURATOR}),
        )
        self.sessions: dict[str, StoredSessionRecord] = {}

    def get_user_by_email(self, email: str) -> StoredUserRecord | None:
        return self.user if email.lower() == self.user.email else None

    def create_session(self, *, session_id: str, user_id: str, token_hash: str, expires_at: datetime, **_kw) -> None:
        self.sessions[token_hash] = StoredSessionRecord(
            session_id=session_id,
            user_id=user_id,
            email=self.user.email,
            display_name=self.user.display_name,
            is_active=self.user.is_active,
            roles=self.user.roles,
            expires_at=expires_at,
        )

    def get_active_session_by_token_hash(self, token_hash: str) -> StoredSessionRecord | None:
        return self.sessions.get(token_hash)

    def revoke_session_by_token_hash(self, token_hash: str) -> bool:
        return self.sessions.pop(token_hash, None) is not None


def test_rbac_and_cookie_session_protection(test_settings: Settings) -> None:
    pwd_hash = hash_password("CuradorClave123!", iterations=10_000)
    repo = InMemoryAuthRepo(pwd_hash)
    auth_svc = AuthService(repository=repo, settings=test_settings)

    session = auth_svc.authenticate_internal_user(email="curador@biolink.org", plain_password="CuradorClave123!")
    assert RoleCode.CURATOR in session.user.roles

    # Validar RBAC directo
    with pytest.raises(AuthorizationError):
        ensure_has_any_role(session.user, [RoleCode.REVIEWER])

    app = create_app(test_settings, health_service=build_fake_health_service(test_settings))
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    @app.get("/api/v1/_test/reviewer-only")
    def _reviewer_only(user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.REVIEWER))]) -> dict:
        return {"user_id": user.user_id}

    @app.get("/api/v1/_test/curator-only")
    def _curator_only(user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR))]) -> dict:
        return {"user_id": user.user_id}

    with TestClient(app) as client:
        # Sin token -> 401
        unauth = client.get("/api/v1/_test/curator-only")
        assert unauth.status_code == 401

        # Token valido en cookie HttpOnly pero sin rol REVIEWER -> 403
        client.cookies.set(test_settings.session_cookie_name, session.raw_token)
        forbidden = client.get("/api/v1/_test/reviewer-only")
        assert forbidden.status_code == 403

        # Rol valido -> 200
        ok = client.get("/api/v1/_test/curator-only")
        assert ok.status_code == 200
        assert ok.json()["user_id"] == "USR_CUR_1"

        # Logout revoca sesion -> 401 posterior
        assert auth_svc.logout(session.raw_token) is True
        revoked = client.get("/api/v1/_test/curator-only")
        assert revoked.status_code == 401
