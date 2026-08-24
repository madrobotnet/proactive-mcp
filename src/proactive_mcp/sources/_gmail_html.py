"""Bounded visible-text extraction from Gmail HTML MIME parts."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Final

from typing_extensions import override

__all__ = ["extract_html_text"]

_HTML_BREAK_TAGS: Final[frozenset[str]] = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)


class _HtmlTextExtractor(HTMLParser):
    """Accumulate visible HTML text, inserting spaces at block boundaries.

    Mutation is required because ``HTMLParser`` delivers tokens incrementally.
    """

    pieces: list[str]

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pieces = []

    @override
    def handle_data(self, data: str) -> None:
        self.pieces.append(data)

    @override
    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in _HTML_BREAK_TAGS:
            self._append_break()

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_BREAK_TAGS:
            self._append_break()

    def _append_break(self) -> None:
        if self.pieces and not self.pieces[-1].endswith((" ", "\t", "\n", "\r")):
            self.pieces.append(" ")


def extract_html_text(html: str, *, max_chars: int) -> tuple[str, bool]:
    """Return stripped visible text bounded to ``max_chars`` and whether it was cut."""
    extractor = _HtmlTextExtractor()
    extractor.feed(html)
    extracted = "".join(extractor.pieces).strip()
    return extracted[:max_chars], len(extracted) > max_chars
