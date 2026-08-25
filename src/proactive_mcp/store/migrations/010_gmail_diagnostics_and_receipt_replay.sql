-- Bounded Gmail diagnostics and digest-only replay-safe delivery receipts.
-- Migration 010 has not shipped: v9 receipt leases are intentionally invalidated
-- here so their raw tokens are never copied into the hardened schema. Situations
-- owned by those short-lived leases remain pending and can be offered again.
PRAGMA secure_delete = ON;
DROP TABLE situation_delivery_claims;

-- This durable marker is cleared only after initialization checkpoints the WAL.
-- It makes erasure completion retryable if a legacy reader pins pre-v10 pages.
CREATE TABLE migration_maintenance (
    task TEXT PRIMARY KEY NOT NULL
        CHECK(task = 'v9_receipt_erasure'),
    pending INTEGER NOT NULL CHECK(pending = 1)
) WITHOUT ROWID;
INSERT INTO migration_maintenance(task, pending)
VALUES ('v9_receipt_erasure', 1);

CREATE TABLE situation_delivery_claims (
    receipt_digest BLOB NOT NULL
        CHECK(typeof(receipt_digest) = 'blob' AND length(receipt_digest) = 32),
    situation_id INTEGER NOT NULL UNIQUE REFERENCES situations(id)
        CHECK(typeof(situation_id) = 'integer'),
    claimed_at TEXT NOT NULL
        CHECK(typeof(claimed_at) = 'text' AND length(claimed_at) > 0),
    expires_at TEXT NOT NULL
        CHECK(typeof(expires_at) = 'text' AND length(expires_at) > 0),
    PRIMARY KEY(receipt_digest, situation_id),
    CHECK(expires_at > claimed_at)
) WITHOUT ROWID;
CREATE INDEX idx_situation_delivery_claims_expiry
ON situation_delivery_claims(expires_at);

CREATE TABLE gmail_diagnostics (
    id INTEGER PRIMARY KEY
        CHECK(typeof(id) = 'integer' AND id = 1),
    outcome TEXT NOT NULL CHECK(
        typeof(outcome) = 'text'
        AND outcome IN ('healthy', 'partial', 'stale', 'auth_error', 'transport_error')
    ),
    request_count INTEGER NOT NULL CHECK(
        typeof(request_count) = 'integer' AND request_count BETWEEN 0 AND 221
    ),
    page_count INTEGER NOT NULL CHECK(
        typeof(page_count) = 'integer' AND page_count BETWEEN 0 AND 20
    ),
    projected_count INTEGER NOT NULL CHECK(
        typeof(projected_count) = 'integer' AND projected_count BETWEEN 0 AND 200
    ),
    excluded_count INTEGER NOT NULL CHECK(
        typeof(excluded_count) = 'integer' AND excluded_count BETWEEN 0 AND 2000
    ),
    byte_budget INTEGER NOT NULL CHECK(
        typeof(byte_budget) = 'integer' AND byte_budget BETWEEN 0 AND 8000000
    )
) WITHOUT ROWID;

CREATE TABLE gmail_diagnostic_reason_counts (
    diagnostic_id INTEGER NOT NULL DEFAULT 1
        REFERENCES gmail_diagnostics(id) ON DELETE CASCADE
        CHECK(typeof(diagnostic_id) = 'integer' AND diagnostic_id = 1),
    reason TEXT NOT NULL CHECK(
        typeof(reason) = 'text'
        AND reason IN (
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
    count INTEGER NOT NULL CHECK(
        typeof(count) = 'integer' AND count BETWEEN 0 AND 200
    ),
    PRIMARY KEY(diagnostic_id, reason)
) WITHOUT ROWID;

CREATE TABLE confirmed_delivery_receipts (
    receipt_digest BLOB PRIMARY KEY NOT NULL
        CHECK(typeof(receipt_digest) = 'blob' AND length(receipt_digest) = 32),
    delivered_count INTEGER NOT NULL CHECK(
        typeof(delivered_count) = 'integer' AND delivered_count BETWEEN 0 AND 100
    ),
    confirmed_at TEXT NOT NULL
        CHECK(typeof(confirmed_at) = 'text' AND length(confirmed_at) > 0)
) WITHOUT ROWID;

CREATE TRIGGER confirmed_delivery_receipts_no_duplicate
BEFORE INSERT ON confirmed_delivery_receipts
WHEN EXISTS (
    SELECT 1 FROM confirmed_delivery_receipts
    WHERE receipt_digest = NEW.receipt_digest
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
