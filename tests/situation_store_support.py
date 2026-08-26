from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from proactive_mcp.store import DeliveryClaim, Detection, SituationEvidence

if TYPE_CHECKING:
    from tests.situation_test_support import FakeClock


def detection(
    key: str,
    *,
    expires_at: datetime | None = None,
) -> Detection:
    return Detection(
        situation_type="reply_deadline",
        dedupe_key=key,
        priority="routine",
        title="Fixture reply deadline",
        why_now="Fixture threshold elapsed",
        evidence=SituationEvidence(facts={"source_id": key}),
        expires_at=expires_at,
    )


def receipt_token(label: str) -> str:
    return f"test-receipt:{label}:{len(label)}"


def delivery_claim(clock: FakeClock, daily_budget: int) -> DeliveryClaim:
    now = clock.now()
    return DeliveryClaim(
        delivered_at=now.isoformat(),
        cooldown_after=(now - timedelta(hours=24)).isoformat(),
        local_day_start=now.replace(hour=0).isoformat(),
        local_day_end=(now.replace(hour=0) + timedelta(days=1)).isoformat(),
        daily_budget=daily_budget,
        allow_noncritical=True,
    )
