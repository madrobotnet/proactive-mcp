"""Credential-safe logging fences for installed-app OAuth dependencies."""

from __future__ import annotations

import logging
from typing import Final, final

from google_auth_oauthlib import flow as oauthlib_flow
from requests_oauthlib import oauth2_session
from typing_extensions import override

_OAUTHLIB_LOGGER_NAME: Final = "google_auth_oauthlib.flow"
_OAUTHLIB_LOGGER: Final = logging.getLogger(_OAUTHLIB_LOGGER_NAME)
_OAUTHLIB_FLOW_SOURCE: Final[str | None] = oauthlib_flow.__file__
_REQUESTS_OAUTHLIB_LOGGER_NAME: Final = "requests_oauthlib.oauth2_session"
_REQUESTS_OAUTHLIB_LOGGER: Final = logging.getLogger(_REQUESTS_OAUTHLIB_LOGGER_NAME)
_REQUESTS_OAUTHLIB_SOURCE: Final[str | None] = oauth2_session.__file__


@final
class _OAuthCallbackAccessLogFilter(logging.Filter):
    """Drop oauthlib loopback request logs without formatting their query."""

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Keep events not emitted by oauthlib's callback request handler."""
        return not (
            record.name == _OAUTHLIB_LOGGER_NAME
            and record.pathname == _OAUTHLIB_FLOW_SOURCE
            and record.funcName == "log_message"
        )


@final
class _OAuthCredentialDebugLogFilter(logging.Filter):
    """Drop credential-bearing DEBUG records from requests-oauthlib itself."""

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Keep other sources and non-DEBUG operational records unchanged."""
        return not (
            record.name == _REQUESTS_OAUTHLIB_LOGGER_NAME
            and record.pathname == _REQUESTS_OAUTHLIB_SOURCE
            and record.levelno == logging.DEBUG
        )


def install_oauth_log_filters() -> None:
    """Install the exact-source callback and credential diagnostic fences."""
    _OAUTHLIB_LOGGER.addFilter(_OAuthCallbackAccessLogFilter())
    _REQUESTS_OAUTHLIB_LOGGER.addFilter(_OAuthCredentialDebugLogFilter())
