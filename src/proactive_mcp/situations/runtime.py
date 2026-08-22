"""Configured Situation engine and Attention policy runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from proactive_mcp.config import ProactiveConfig, load_config, resolve_timezone

from .engine import SituationEngine
from .policy import AttentionPolicy

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.clock import Clock
    from proactive_mcp.store import Store

__all__ = ["SituationRuntime"]


@dataclass(frozen=True, slots=True)
class SituationRuntime:
    """One config-consistent detector and delivery-policy pair."""

    engine: SituationEngine
    attention: AttentionPolicy
    config: ProactiveConfig

    @classmethod
    def from_config(
        cls,
        store: Store,
        clock: Clock,
        config_path: Path,
    ) -> Self:
        """Load config once and wire every M3 policy consumer."""
        config = load_config(config_path)
        timezone = resolve_timezone(config.attention.timezone, now=clock.now())
        return cls(
            engine=SituationEngine(store, clock, timezone, config.detectors),
            attention=AttentionPolicy(
                store.situations,
                timezone,
                config.attention,
            ),
            config=config,
        )
