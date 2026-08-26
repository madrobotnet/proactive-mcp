from __future__ import annotations

import importlib
import pickle
from dataclasses import dataclass
from typing import cast

import pytest

from proactive_mcp.config import ConfigError
from proactive_mcp.sources import GoogleSourceConfigurationError
from proactive_mcp.sources.credentials import (
    CredentialKeyring,
    CredentialScopeError,
    CredentialStorageError,
    GoogleCredential,
    MissingRefreshTokenError,
)
from proactive_mcp.sources.gmail import (
    GmailAuthError,
    GmailError,
    GmailHttpResponse,
    GmailInboxReadResult,
    GmailParseError,
    GmailProfile,
    GmailReadResult,
    GmailThread,
)
from proactive_mcp.sources.oauth import (
    GoogleClientConfig,
    GoogleInstalledApplicationConfig,
    InstalledAppFlowFactory,
    LocalInstalledAppFlow,
)
from proactive_mcp.store.situations import DetectionSourceMismatchError
from proactive_mcp.store.sync import SourceSyncState


@dataclass(frozen=True, slots=True)
class _PublicSymbol:
    module: str
    name: str
    value: type[object]


_PUBLIC_SYMBOLS = (
    _PublicSymbol("proactive_mcp.config", "ConfigError", ConfigError),
    _PublicSymbol("proactive_mcp.sources.gmail", "GmailError", GmailError),
    _PublicSymbol("proactive_mcp.sources.gmail", "GmailAuthError", GmailAuthError),
    _PublicSymbol("proactive_mcp.sources.gmail", "GmailParseError", GmailParseError),
    _PublicSymbol(
        "proactive_mcp.sources.gmail", "GmailHttpResponse", GmailHttpResponse
    ),
    _PublicSymbol("proactive_mcp.sources.gmail", "GmailProfile", GmailProfile),
    _PublicSymbol("proactive_mcp.sources.gmail", "GmailThread", GmailThread),
    _PublicSymbol("proactive_mcp.sources.gmail", "GmailReadResult", GmailReadResult),
    _PublicSymbol(
        "proactive_mcp.sources.gmail",
        "GmailInboxReadResult",
        GmailInboxReadResult,
    ),
    _PublicSymbol(
        "proactive_mcp.sources.oauth",
        "GoogleInstalledApplicationConfig",
        GoogleInstalledApplicationConfig,
    ),
    _PublicSymbol(
        "proactive_mcp.sources.oauth", "GoogleClientConfig", GoogleClientConfig
    ),
    _PublicSymbol(
        "proactive_mcp.sources.oauth", "LocalInstalledAppFlow", LocalInstalledAppFlow
    ),
    _PublicSymbol(
        "proactive_mcp.sources.oauth",
        "InstalledAppFlowFactory",
        InstalledAppFlowFactory,
    ),
    _PublicSymbol(
        "proactive_mcp.store.sync",
        "SourceSyncState",
        SourceSyncState,
    ),
    _PublicSymbol(
        "proactive_mcp.store.situations",
        "DetectionSourceMismatchError",
        DetectionSourceMismatchError,
    ),
    _PublicSymbol(
        "proactive_mcp.sources",
        "GoogleSourceConfigurationError",
        GoogleSourceConfigurationError,
    ),
    _PublicSymbol(
        "proactive_mcp.sources.credentials",
        "MissingRefreshTokenError",
        MissingRefreshTokenError,
    ),
    _PublicSymbol(
        "proactive_mcp.sources.credentials",
        "CredentialScopeError",
        CredentialScopeError,
    ),
    _PublicSymbol(
        "proactive_mcp.sources.credentials",
        "CredentialStorageError",
        CredentialStorageError,
    ),
    _PublicSymbol(
        "proactive_mcp.sources.credentials", "GoogleCredential", GoogleCredential
    ),
    _PublicSymbol(
        "proactive_mcp.sources.credentials", "CredentialKeyring", CredentialKeyring
    ),
)


@pytest.mark.parametrize(
    "symbol",
    _PUBLIC_SYMBOLS,
    ids=tuple(symbol.name for symbol in _PUBLIC_SYMBOLS),
)
def test_extracted_public_symbol_retains_import_identity(symbol: _PublicSymbol) -> None:
    resolved = cast("object", vars(importlib.import_module(symbol.module))[symbol.name])

    assert symbol.value.__module__ == symbol.module
    assert resolved is symbol.value
    restored = cast("object", pickle.loads(pickle.dumps(symbol.value)))  # noqa: S301
    assert restored is symbol.value


_PICKLE_VALUES = (
    GmailError("unknown"),
    GmailAuthError("http_4xx"),
    GmailParseError("unknown"),
    GmailHttpResponse(200, b"{}"),
    GmailProfile("fixture@example.test", 1, 1, "history"),
    GmailThread("thread", "history"),
    GmailReadResult(
        threads=(),
        fetched_at="2026-01-01T00:00:00+00:00",
        page_count=1,
        skipped_count=0,
        is_complete=True,
        degradation_reasons=(),
    ),
    GmailInboxReadResult(
        threads=(),
        fetched_at="2026-01-01T00:00:00+00:00",
        provider_history_cursor="history",
        page_count=1,
        coverage_complete=True,
        degradation_reasons=(),
    ),
)


@pytest.mark.parametrize(
    "value",
    _PICKLE_VALUES,
    ids=tuple(type(value).__name__ for value in _PICKLE_VALUES),
)
def test_extracted_public_value_pickle_roundtrip(value: object) -> None:
    restored = cast("object", pickle.loads(pickle.dumps(value)))  # noqa: S301

    assert restored == value
    assert type(restored) is type(value)
