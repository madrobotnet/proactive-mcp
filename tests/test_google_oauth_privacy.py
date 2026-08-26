from __future__ import annotations

import logging
import socket
import threading
from typing import cast
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
from google_auth_oauthlib import flow as oauthlib_flow
from requests_oauthlib import oauth2_session

from tests.google_oauth_support import (
    CallbackCanaryApplication,
)


@pytest.mark.parametrize(
    ("request_target", "canaries"),
    [
        (
            "/?code=oauth-code-canary&state=oauth-state-canary",
            ("oauth-code-canary", "oauth-state-canary"),
        ),
        (
            "/?error=access_denied&error_description=malformed-%ZZ-canary",
            ("malformed-%ZZ-canary",),
        ),
    ],
)
def test_loopback_callback_access_log_hides_oauth_query_canaries(
    request_target: str,
    canaries: tuple[str, ...],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = (
        f"GET {request_target} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
    ).encode()
    app = CallbackCanaryApplication()
    handler_class = cast(
        "type[WSGIRequestHandler]",
        vars(oauthlib_flow)["_WSGIRequestHandler"],
    )
    server = make_server("127.0.0.1", 0, app, handler_class=handler_class)
    server.timeout = 2
    handler_started = threading.Event()
    handler_stopped = threading.Event()

    def handle_callback() -> None:
        handler_started.set()
        try:
            server.handle_request()
        finally:
            handler_stopped.set()

    caplog.set_level(logging.INFO, logger="google_auth_oauthlib.flow")
    thread = threading.Thread(target=handle_callback, name="oauth-callback-canary")
    thread.start()
    response = b""
    try:
        assert handler_started.wait(timeout=2)
        with socket.create_connection(
            ("127.0.0.1", server.server_port), timeout=2
        ) as callback:
            callback.settimeout(2)
            callback.sendall(request)
            while chunk := callback.recv(4096):
                response += chunk
        assert app.callback_received.wait(timeout=2)
        assert handler_stopped.wait(timeout=2)
    finally:
        server.server_close()
        thread.join(timeout=3)

    assert not thread.is_alive()
    logging.getLogger("google_auth_oauthlib.flow").info("oauth.non_access_canary")
    captured = capsys.readouterr()
    combined_output = "\n".join(
        (
            captured.out,
            captured.err,
            *(record.getMessage() for record in caplog.records),
        )
    )
    assert app.request_target == request_target
    assert b"200 OK" in response
    assert b"callback complete" in response
    assert "oauth.non_access_canary" in combined_output
    assert all(canary not in combined_output for canary in canaries)


def test_requests_oauthlib_debug_credentials_are_fenced_at_the_exact_source(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canaries = (
        "oauth-state-canary",
        "oauth-code-canary",
        "client-secret-canary",
        "access-token-canary",
        "refresh-token-canary",
        "/private/oauth-path-canary",
    )
    logger = logging.getLogger("requests_oauthlib.oauth2_session")
    source = oauth2_session.__file__
    assert source is not None
    caplog.set_level(logging.DEBUG)

    for canary in canaries:
        record = logger.makeRecord(
            logger.name,
            logging.DEBUG,
            source,
            1,
            "upstream OAuth diagnostic %s",
            (canary,),
            None,
            "fetch_token",
        )
        logger.handle(record)

    for level, message in (
        (logging.INFO, "oauth-info-diagnostic-canary"),
        (logging.WARNING, "oauth-warning-diagnostic-canary"),
    ):
        record = logger.makeRecord(
            logger.name,
            level,
            source,
            1,
            message,
            (),
            None,
            "fetch_token",
        )
        logger.handle(record)

    captured = capsys.readouterr()
    combined_output = "\n".join(
        (
            captured.out,
            captured.err,
            *(record.getMessage() for record in caplog.records),
        )
    )
    assert all(combined_output.count(canary) == 0 for canary in canaries)
    assert "oauth-info-diagnostic-canary" in combined_output
    assert "oauth-warning-diagnostic-canary" in combined_output


def test_oauthlib_same_template_non_callback_log_survives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="google_auth_oauthlib.flow")

    logging.getLogger("google_auth_oauthlib.flow").info(
        '"%s" %s %s',
        "generic-non-callback-canary",
        201,
        12,
    )

    assert any(
        record.getMessage() == '"generic-non-callback-canary" 201 12'
        for record in caplog.records
    )
