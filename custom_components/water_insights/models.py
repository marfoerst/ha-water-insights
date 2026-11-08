"""Plain data models (no Home Assistant imports)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .const import (
    DEFAULT_END_QUIET_S,
    DEFAULT_MAX_FLOW,
    DEFAULT_MIN_VOLUME_L,
    DEFAULT_START_FLOW,
)


@dataclass
class DetectorConfig:
    """Thresholds defining what counts as a water draw."""

    start_flow: float = DEFAULT_START_FLOW
    end_quiet_s: float = DEFAULT_END_QUIET_S
    min_volume_l: float = DEFAULT_MIN_VOLUME_L
    max_flow: float = DEFAULT_MAX_FLOW


@dataclass
class WaterEvent:
    """A single detected water draw."""

    id: str
    start: float
    end: float
    volume_l: float
    duration_s: float
    max_flow: float
    samples: list[tuple[float, float]] = field(default_factory=list)
    helpers: dict[str, float] = field(default_factory=dict)
    label: str | None = None
    label_source: str | None = None
    prediction: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        data = asdict(self)
        data["samples"] = [[round(t, 1), round(f, 3)] for t, f in self.samples]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WaterEvent:
        """Deserialize from dict."""
        return cls(
            id=str(data["id"]),
            start=float(data["start"]),
            end=float(data["end"]),
            volume_l=float(data["volume_l"]),
            duration_s=float(data["duration_s"]),
            max_flow=float(data.get("max_flow", 0.0)),
            samples=[(float(t), float(f)) for t, f in data.get("samples", [])],
            helpers={k: float(v) for k, v in (data.get("helpers") or {}).items()},
            label=data.get("label"),
            label_source=data.get("label_source"),
            prediction=data.get("prediction"),
            confidence=data.get("confidence"),
        )
