from __future__ import annotations

import re
from datetime import datetime, timezone

_PARTIAL_DATE_PATTERN = re.compile(r"^\d{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?$")


def utc_now() -> datetime:
    """Retorna marca temporal UTC consciente de zona horaria."""
    return datetime.now(timezone.utc)


def validate_partial_iso_date(value: str) -> str:
    """Valida fecha ISO-8601 parcial (YYYY, YYYY-MM o YYYY-MM-DD) sin inventar dias (RD-09)."""
    cleaned = value.strip()
    if not _PARTIAL_DATE_PATTERN.match(cleaned):
        raise ValueError(
            f"Formato temporal invalido '{value}'. Use YYYY, YYYY-MM o YYYY-MM-DD conforme a RD-09."
        )
    return cleaned


def partial_date_upper_bound(value: str) -> str:
    """Extiende un limite superior parcial solo para comparaciones de rango sin mutar el dato."""
    cleaned = validate_partial_iso_date(value)
    if len(cleaned) == 4:
        return f"{cleaned}-12-31"
    if len(cleaned) == 7:
        return f"{cleaned}-31"
    return cleaned
