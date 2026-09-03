"""Interactive input boundary for Google setup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias, final

if TYPE_CHECKING:
    from argparse import ArgumentParser
    from typing import TextIO

__all__ = [
    "SetupWizardAnswers",
    "SetupWizardDefaults",
    "SetupWizardInputError",
    "add_setup_arguments",
    "collect_setup_wizard_answers",
    "resolve_setup_client_secrets_path",
]

_INPUT_ERROR_MESSAGE: Final = (
    "Interactive setup input is unavailable; use --non-interactive."
)
_OAUTH_CLIENT_PATH_PROMPT: Final = "OAuth client JSON path: "
_OPEN_BROWSER_PROMPT: Final = "Open a browser on this device for authorization? [y/n]: "
_BROWSER_ANSWER_RETRY: Final = "Please answer yes or no.\n"


@dataclass(frozen=True, slots=True)
class SetupWizardAnswers:
    """Google setup values supplied by defaults or interactive input."""

    client_secrets_path: Path
    headless: bool


SetupWizardDefaults: TypeAlias = SetupWizardAnswers


@final
class SetupWizardInputError(Exception):
    """Signal that interactive setup input cannot be safely collected."""

    def __init__(self) -> None:
        """Expose a fixed recovery message without input details."""
        Exception.__init__(self, _INPUT_ERROR_MESSAGE)


def add_setup_arguments(parser: ArgumentParser) -> None:
    """Register setup-specific command-line options."""
    _ = parser.add_argument(
        "--reauth",
        action="store_true",
        help="replace the current Google authorization",
    )
    _ = parser.add_argument(
        "--headless",
        action="store_true",
        help="do not launch a browser for loopback authorization",
    )
    _ = parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="do not prompt for setup input",
    )
    _ = parser.add_argument(
        "--client-secrets",
        metavar="PATH",
        help="path to an installed-app OAuth client secret file",
    )


def resolve_setup_client_secrets_path(
    explicit_path: Path | None,
    environment_path: str | None,
    state_directory: Path,
) -> Path:
    """Resolve the setup client-secret path by its documented precedence."""
    if explicit_path is not None:
        return explicit_path
    if environment_path is not None:
        return Path(environment_path)
    return state_directory / "client_secret.json"


def collect_setup_wizard_answers(
    defaults: SetupWizardDefaults,
    stdin: TextIO,
    stdout: TextIO,
) -> SetupWizardAnswers:
    """Collect OAuth setup choices without disclosing values in output."""
    try:
        if not stdin.isatty():
            raise SetupWizardInputError

        _ = stdout.write(_OAUTH_CLIENT_PATH_PROMPT)
        stdout.flush()
        path_answer = stdin.readline()
        if path_answer == "":
            raise SetupWizardInputError
        client_secrets_path = (
            defaults.client_secrets_path
            if not path_answer.strip()
            else Path(path_answer.strip())
        )

        while True:
            _ = stdout.write(_OPEN_BROWSER_PROMPT)
            stdout.flush()
            browser_answer = stdin.readline()
            if browser_answer == "":
                raise SetupWizardInputError
            normalized_browser_answer = browser_answer.strip().lower()
            if normalized_browser_answer in {"y", "yes"}:
                headless = False
            elif normalized_browser_answer in {"n", "no"}:
                headless = True
            elif not normalized_browser_answer:
                headless = defaults.headless
            else:
                _ = stdout.write(_BROWSER_ANSWER_RETRY)
                continue
            return SetupWizardAnswers(
                client_secrets_path=client_secrets_path,
                headless=headless,
            )
    except OSError:
        raise SetupWizardInputError from None
