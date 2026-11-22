from __future__ import annotations

import re
from dataclasses import dataclass

from biolink_backend.shared.exceptions import DomainValidationError
from biolink_backend.shared.time import validate_partial_iso_date

_SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


@dataclass(frozen=True, slots=True)
class PartialDateRange:
    """Intervalo temporal que preserva la precision publicada sin inventar dias (RD-09)."""

    period_start: str
    period_end: str

    def __post_init__(self) -> None:
        try:
            start = validate_partial_iso_date(self.period_start)
            end = validate_partial_iso_date(self.period_end)
        except ValueError as exc:
            raise DomainValidationError(str(exc)) from exc

        if end < start:
            raise DomainValidationError(
                f"El periodo final '{end}' no puede ser anterior al inicial '{start}'.",
                details={"period_start": start, "period_end": end},
            )
        object.__setattr__(self, "period_start", start)
        object.__setattr__(self, "period_end", end)


@dataclass(frozen=True, slots=True)
class ChecksumSHA256:
    """Hash criptografico SHA-256 para integridad e inmutabilidad de origen (RNF-04)."""

    value: str

    def __post_init__(self) -> None:
        cleaned = self.value.strip().lower()
        if not _SHA256_PATTERN.match(cleaned):
            raise DomainValidationError(
                "Checksum SHA-256 invalido: debe contener exactamente 64 caracteres hexadecimales."
            )
        object.__setattr__(self, "value", cleaned)
