-- Bounded Gmail read diagnostics and replay-safe confirmed delivery receipts.
CREATE TABLE gmail_diagnostics (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    outcome TEXT NOT NULL CHECK(
        outcome IN ('healthy', 'partial', 'stale', 'auth_error', 'transport_error')
    ),
    request_count INTEGER NOT NULL CHECK(request_count >= 0),
    page_count INTEGER NOT NULL CHECK(page_count >= 0),
    projected_count INTEGER NOT NULL CHECK(projected_count >= 0),
    excluded_count INTEGER NOT NULL CHECK(excluded_count >= 0),
    byte_budget INTEGER NOT NULL CHECK(byte_budget >= 0)
);

CREATE TABLE gmail_diagnostic_reason_counts (
    diagnostic_id INTEGER NOT NULL DEFAULT 1
        REFERENCES gmail_diagnostics(id) ON DELETE CASCADE
        CHECK(diagnostic_id = 1),
    reason TEXT NOT NULL CHECK(
        reason IN (
            'body_snippet_fallback',
            'body_truncated',
            'degraded',
            'direction_metadata_ambiguous',
            'direction_metadata_missing',
            'http_4xx',
            'http_5xx',
            'identity_headers_ambiguous',
            'invalid_grant',
            'mime_structure_truncated',
            'network',
            'never_synced',
            'not_configured',
            'pagination_limit',
            'resource_limit',
            'scope_mismatch',
            'stale',
            'sync_budget_exhausted',
            'thread_list_entry_skipped',
            'thread_projection_limit',
            'thread_response_too_large',
            'thread_without_projectable_message',
            'timeout',
            'unknown'
        )
    ),
    count INTEGER NOT NULL CHECK(count >= 0),
    PRIMARY KEY(diagnostic_id, reason)
);

CREATE TABLE confirmed_delivery_receipts (
    receipt_token TEXT PRIMARY KEY NOT NULL CHECK(length(receipt_token) > 0),
    delivered_count INTEGER NOT NULL CHECK(delivered_count >= 0),
    confirmed_at TEXT NOT NULL CHECK(length(confirmed_at) > 0)
);

CREATE TRIGGER confirmed_delivery_receipts_no_duplicate
BEFORE INSERT ON confirmed_delivery_receipts
WHEN EXISTS (
    SELECT 1 FROM confirmed_delivery_receipts
    WHERE receipt_token = NEW.receipt_token
)
BEGIN
    SELECT RAISE(ABORT, 'confirmed delivery receipts are immutable');
END;

CREATE TRIGGER confirmed_delivery_receipts_no_update
BEFORE UPDATE ON confirmed_delivery_receipts
BEGIN
    SELECT RAISE(ABORT, 'confirmed delivery receipts are immutable');
END;

CREATE TRIGGER confirmed_delivery_receipts_no_delete
BEFORE DELETE ON confirmed_delivery_receipts
BEGIN
    SELECT RAISE(ABORT, 'confirmed delivery receipts are immutable');
END;
