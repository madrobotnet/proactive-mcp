"""Bounded Gmail wire projection for deterministic reply detection."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import ClassVar, Final, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from proactive_mcp.situations.inputs import (
    InboxThreadDegradationReason,
    InboxThreadSnapshot,
)

from ._gmail_html import extract_html_text

ProjectionDegradationReason: TypeAlias = InboxThreadDegradationReason
_MILLISECONDS_PER_SECOND: Final[int] = 1000
_MAX_BODY_CHARS: Final[int] = 4_000
_MAX_MIME_DEPTH: Final[int] = 8
_MAX_MIME_PARTS: Final[int] = 64


class _Wire(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class _WireHeader(_Wire):
    name: str
    value: str


class _WireBody(_Wire):
    data: str | None = None


class _WirePart(_Wire):
    mime_type: str | None = Field(default=None, alias="mimeType")
    headers: tuple[_WireHeader, ...] = ()
    body: _WireBody | None = None
    parts: tuple[_WirePart, ...] = ()


class _WireMessage(_Wire):
    id: str | None = None
    internal_date: int | None = Field(default=None, alias="internalDate")
    snippet: str | None = None
    label_ids: tuple[str, ...] | None = Field(default=None, alias="labelIds")
    payload: _WirePart | None = None


class WireThreadDetail(_Wire):
    messages: tuple[_WireMessage, ...] = ()


THREAD_DETAIL_ADAPTER: Final[TypeAdapter[WireThreadDetail]] = TypeAdapter(
    WireThreadDetail
)


def project_thread(
    thread_id: str,
    wire: WireThreadDetail,
    *,
    profile_email: str,
    profile_history_cursor: str,
) -> InboxThreadSnapshot | None:
    """Project one full Gmail thread into the bounded detector input."""
    candidates: list[tuple[datetime, str, _WireMessage]] = []
    for message in wire.messages:
        if message.id is None or message.internal_date is None:
            continue
        try:
            sent_at = datetime.fromtimestamp(
                message.internal_date / _MILLISECONDS_PER_SECOND,
                tz=UTC,
            )
        except (OSError, OverflowError, ValueError):
            continue
        candidates.append((sent_at, message.id, message))
    if not candidates:
        return None
    sent_at, message_id, latest = max(candidates, key=lambda item: (item[0], item[1]))
    headers, identity_headers_ambiguous = _headers(latest.payload)
    sender_name, sender_address = parseaddr(headers.get("from", ""))
    del profile_email
    sent, inbox, direction_reasons = _trusted_direction(latest.label_ids)
    body = _project_body(latest.payload)
    body_text = body.text
    reasons = list(direction_reasons)
    if identity_headers_ambiguous:
        reasons.append("identity_headers_ambiguous")
    if body_text is None:
        body_text = latest.snippet
        reasons.append("body_snippet_fallback")
    elif body.truncated:
        reasons.append("body_truncated")
    elif body.structure_truncated:
        reasons.append("mime_structure_truncated")
    degradation_reasons = tuple(dict.fromkeys(reasons))
    return InboxThreadSnapshot(
        thread_id=thread_id,
        latest_message_id=message_id,
        latest_from_user=sent,
        user_is_recipient=inbox,
        latest_message_at=sent_at,
        subject=headers.get("subject") or None,
        sender_display=sender_name or sender_address or None,
        snippet=latest.snippet,
        body_text=body_text,
        resolution_safe=not degradation_reasons,
        degradation_reasons=degradation_reasons,
        provider_history_cursor=profile_history_cursor,
    )


def _trusted_direction(
    label_ids: tuple[str, ...] | None,
) -> tuple[bool, bool, tuple[ProjectionDegradationReason, ...]]:
    labels = frozenset(label_ids or ())
    sent = "SENT" in labels
    inbox = "INBOX" in labels
    if not labels:
        reasons: tuple[ProjectionDegradationReason, ...] = (
            "direction_metadata_missing",
        )
    elif (sent and inbox) or not (sent or inbox):
        reasons = ("direction_metadata_ambiguous",)
    else:
        reasons = ()
    return sent, inbox, reasons


def _headers(payload: _WirePart | None) -> tuple[dict[str, str], bool]:
    if payload is None:
        return {}, False
    values: dict[str, str] = {}
    ambiguous = False
    identity_names = {"from", "to", "cc", "bcc"}
    for header in payload.headers:
        name = header.name.casefold()
        if name in identity_names and name in values:
            ambiguous = True
        values[name] = header.value
    return values, ambiguous


@dataclass(frozen=True, slots=True)
class _BodyProjection:
    text: str | None
    truncated: bool
    structure_truncated: bool


def _project_body(part: _WirePart | None) -> _BodyProjection:
    if part is None:
        return _BodyProjection(
            text=None,
            truncated=False,
            structure_truncated=False,
        )
    stack: list[tuple[_WirePart, int]] = [(part, 0)]
    seen = 0
    html_text: str | None = None
    body_truncated = False
    structure_truncated = False
    while stack:
        current, depth = stack.pop()
        seen += 1
        if seen > _MAX_MIME_PARTS or depth > _MAX_MIME_DEPTH:
            structure_truncated = True
            continue
        mime_type = (current.mime_type or "").casefold()
        decoded, truncated = _decode_body(current)
        body_truncated = body_truncated or truncated
        if decoded is not None and mime_type == "text/plain":
            return _BodyProjection(
                decoded,
                body_truncated,
                structure_truncated,
            )
        if decoded is not None and mime_type == "text/html" and html_text is None:
            html_text, html_truncated = extract_html_text(
                decoded,
                max_chars=_MAX_BODY_CHARS,
            )
            body_truncated = body_truncated or html_truncated
        stack.extend((child, depth + 1) for child in reversed(current.parts))
    return _BodyProjection(html_text, body_truncated, structure_truncated)


def _decode_body(part: _WirePart) -> tuple[str | None, bool]:
    if part.body is None or part.body.data is None:
        return None, False
    encoded = part.body.data
    maximum_encoded = ((_MAX_BODY_CHARS * 4) // 3 + 8) // 4 * 4
    bounded = encoded[:maximum_encoded]
    padded = bounded + "=" * (-len(bounded) % 4)
    try:
        decoded = base64.b64decode(
            padded,
            altchars=b"-_",
            validate=True,
        ).decode(errors="ignore")
    except binascii.Error:
        return None, False
    truncated = len(encoded) > len(bounded) or len(decoded) > _MAX_BODY_CHARS
    return decoded[:_MAX_BODY_CHARS], truncated


__all__ = [
    "THREAD_DETAIL_ADAPTER",
    "ProjectionDegradationReason",
    "WireThreadDetail",
    "project_thread",
]
