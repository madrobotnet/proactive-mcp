from datetime import timedelta
from pathlib import Path

from proactive_mcp.paths import ProactivePaths
from proactive_mcp.server.status import status_response
from proactive_mcp.store import FallbackClaim, Store
from tests.situation_test_support import FakeClock, utc_datetime
from tests.situation_tool_support import UNTRUSTED_SUBJECT, pending_detection

_PID = 4242
_FALLBACK_WAIT = timedelta(minutes=30)
_FALLBACK_COUNT = 25
_PAGE_SIZE = 20


def test_status_counts_fallback_outcomes_past_the_situation_page(
    tmp_path: Path,
) -> None:
    # Given: more persisted fallbacks than one situation page, with the only
    # failure after situation id 20, on an otherwise healthy installation.
    paths = ProactivePaths.for_database(tmp_path / "proactive.db")
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(paths.database, clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail")
        store.record_sync_success("calendar")
        _ = store.situations.upsert_detections(
            tuple(
                pending_detection(f"fallback-{index}", "critical")
                for index in range(_FALLBACK_COUNT)
            )
        )
        clock.advance(_FALLBACK_WAIT + timedelta(minutes=1))
        store.daemon.record_start(pid=_PID)
        store.daemon.record_heartbeat()
        claim = FallbackClaim(
            claimed_at=clock.now().isoformat(),
            detected_before=(clock.now() - _FALLBACK_WAIT).isoformat(),
            priorities=("critical",),
        )
        claimed = tuple(
            store.fallbacks.claim_next(claim) for _ in range(_FALLBACK_COUNT)
        )
        assert all(item is not None for item in claimed)
        assert claimed[-1] is not None
        assert claimed[-1].id > _PAGE_SIZE
        for item in claimed[:-1]:
            assert item is not None
            store.fallbacks.record_sent(item.id)
        store.fallbacks.record_failed(claimed[-1].id, code="timeout")

        # When: get_status builds the redacted fallback surface.
        status = status_response(store, clock, paths)

    # Then: every persisted outcome is counted, including the late failure.
    assert status.fallback.claimed == 0
    assert status.fallback.sent == _FALLBACK_COUNT - 1
    assert status.fallback.failed == 1
    assert status.fallback.failure_codes == ("timeout",)
    assert status.overall == "degraded"
    assert status.warnings == ("OS notification fallback failed for 1 situation(s).",)
    assert UNTRUSTED_SUBJECT not in status.model_dump_json()
