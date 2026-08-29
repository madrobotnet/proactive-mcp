# State model

The MCP response keeps legacy protocol-v1 fields while exposing independent
operational dimensions. Clients must not infer one dimension from another.

## Source state

Each Gmail and Calendar response retains the legacy `status`, timestamps, and
`error_code`, and adds:

- `authorization.state`: `not_configured`, `configured`, `needs_reauth`,
  `scope_mismatch`, `credential_missing`, or `credential_unavailable`.
- `freshness.state`: `never_synced`, `fresh`, or `stale`.
- `read.state`: `never_attempted`, `complete`, `partial`, `auth_error`,
  `transport_error`, `resource_error`, or `parse_error`.
- `generation.state`: `current`, `syncing`, `degraded`, or `interrupted`, plus
  issued/applied generation numbers.

`generation=syncing` becomes `interrupted` when an issued generation remains
unapplied for more than ten minutes. Delivery eligibility continues to fail
closed while the latest generation is not current and complete.

Source sync state, Gmail diagnostics, both generation rows, and credential
availability are read from one SQLite statement snapshot. Temporary credential
storage failures remain `credential_unavailable` across coalesced checks and
clear only after a later credential load reports `available` or `missing`.
`credential_missing` distinguishes a configured source whose credential was
removed from a source that was never configured.

## Situation delivery state

`Situation.state` remains the lifecycle state for compatibility. The additive
`delivery` object reports receipt state:

- `available`: pending and not leased.
- `leased`: reserved by an unexpired host receipt.
- `host_confirmed`: the host confirmed the whole lease.
- `not_applicable`: a non-deliverable lifecycle state with no prior host
  confirmation.

For `pending`, the current lease always takes precedence over historical
delivery timestamps. Waking or reactivating a Situation clears lifecycle
timestamps from its previous delivery cycle; immutable delivery history remains
in `situation_deliveries`.

`delivery.presentation` is `unknown`. A host confirmation proves receipt by the
host, not that every candidate was shown to the user. Per-item
`presented|suppressed|deferred` outcomes require a later protocol revision in
which the host reports them.

`source.name` and `source.generation` provide bounded provenance without
provider record identifiers. Source-generation absence resolution is scoped to
that source and never resolves compatibility rows with `source.name=local`.

## Collector readiness

The store records only profile and timestamps for successful MCP calls. It does
not store host, model, conversation, or user identity.

- `never_seen`: no `proactive_check` was observed for the profile.
- `active`: a check was observed within 24 hours.
- `stale`: the latest observed check is older than 24 hours.

Observation timestamps are normalized to UTC and only move forward, so a
late-finishing older call cannot overwrite a newer observation.

`system_health` and `delivery_readiness` are separate. Healthy local storage and
fresh sources do not prove that an external host is currently collecting.

## Daemon and fallback

Daemon `liveness` remains independent from `last_run_state`:

- liveness: `never_started`, `running`, `stale`, `stopped`
- last run: `never_run`, `unknown`, `succeeded`, `degraded`, `failed`

`unknown` identifies a migrated or started row whose prior result cannot be
truthfully inferred. A failed owned run persists only bounded phase, code, and
timestamps, and non-failed rows reject any partial failure metadata.

Fallback state is `disabled`, `unavailable`, `healthy`, or `degraded`.
`[fallback] enabled = false` is the supported headless/self-hosted setting.
`state` prioritizes current capability, while `history_state` independently
reports `healthy|degraded` persisted delivery history.

## Database and receipts

A successfully opened store reports legacy `status=healthy` and
`health=ready`. The health type reserves `starting`, `maintenance`,
`read_only`, and `unavailable` for an outer process health boundary that can
answer when the store itself cannot open.

An unknown or expired supplied receipt returns
`status=invalid_or_expired, delivered_count=0`. The combined value avoids
revealing whether a receipt previously existed. A missing required input still
fails MCP schema validation.

## Migration 011

Migration 011 is additive. It:

- timestamps issued and applied source generations;
- stores bounded credential availability without credential material;
- stores bounded Situation source provenance;
- records profile-scoped collector check/confirmation timestamps;
- records daemon mode and the latest bounded run outcome.

Legacy response fields and protocol version 1 remain available. Existing
Situation rows receive source names derived from their type; historical source
generation numbers remain null because they cannot be reconstructed safely.
