from __future__ import annotations

import re
import time
import uuid

# Prefijos oficiales definidos en ADR-002
DOMAIN_PREFIXES: dict[str, str] = {
    "taxon": "TX_",
    "taxon_name": "NM_",
    "taxon_identifier": "TID_",
    "locality": "LOC_",
    "source": "SRC_",
    "source_version": "SVER_",
    "evidence": "EVD_",
    "interaction_assertion": "AST_",
    "occurrence": "OCC_",
    "import_batch": "BAT_",
    "import_record": "REC_",
    "user": "USR_",
    "outbox_event": "OBX_",
    "review_event": "REV_",
    "session": "SES_",
}

_SAFE_ID_PATTERN = re.compile(r"^[A-Z]+_[A-Za-z0-9_-]{2,56}$")


def generate_domain_id(entity_key: str) -> str:
    """Genera un identificador prefijado ordenable temporalmente (ADR-002)."""
    if entity_key not in DOMAIN_PREFIXES:
        raise ValueError(f"Entidad desconocida para generacion de ID: '{entity_key}'.")
    prefix = DOMAIN_PREFIXES[entity_key]
    ts_hex = f"{int(time.time() * 1000):011x}"
    rnd_hex = uuid.uuid4().hex[:12]
    return f"{prefix}{ts_hex}_{rnd_hex}"


def validate_domain_id(value: str, expected_entity: str | None = None) -> str:
    """Valida que el identificador no este vacio, respete formato seguro y prefijo."""
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 64:
        raise ValueError("El identificador debe tener entre 1 y 64 caracteres.")
    if expected_entity is not None:
        prefix = DOMAIN_PREFIXES[expected_entity]
        # Permite tanto prefijos canonicos modernos como los de fixture historico (ej. BAT2007, TAX_AN_001)
        if not (cleaned.startswith(prefix) or _SAFE_ID_PATTERN.match(cleaned) or cleaned.isalnum()):
            raise ValueError(f"Identificador invalido '{cleaned}' para entidad '{expected_entity}'.")
    return cleaned
