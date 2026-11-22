from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class EngineHealthSchema(BaseModel):
    healthy: bool
    latency_ms: float
    version: str | None = None
    error: str | None = None


class LivenessResponse(BaseModel):
    status: Literal["alive"]
    service: str
    version: str
    environment: str
    uptime_seconds: float
    timestamp_utc: datetime


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    ready: bool
    timestamp_utc: datetime
    dependencies: dict[str, EngineHealthSchema]


class ConsolidatedHealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    live: bool
    ready: bool
    service: str
    version: str
    environment: str
    uptime_seconds: float
    timestamp_utc: datetime
    memory_budget_mb: dict[str, int]
    dependencies: dict[str, EngineHealthSchema]
