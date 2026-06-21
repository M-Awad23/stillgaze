from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


MAX_PAGE_BYTES = 1_500_000
MAX_CONTEXT_CHARS = 12_000


class WebReadError(RuntimeError):
    pass


class ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.skip_depth = 0
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "br", "li", "div", "section", "article", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"} and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        else:
            self.text_parts.append(text)

    @property
    def title(self) -> str:
        return normalize_text(" ".join(self.title_parts))[:180]

    @property
    def text(self) -> str:
        return normalize_text(" ".join(self.text_parts))


def normalize_text(value: str) -> str:
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebReadError("Enter a valid http or https URL.")
    return url


def read_web_page(url: str) -> dict[str, str | int]:
    url = validate_url(url)
    request = Request(
        url,
        headers={
            "User-Agent": "StillGaze/0.1 (+local web reader)",
            "Accept": "text/html, text/plain;q=0.9, */*;q=0.8",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=12) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(MAX_PAGE_BYTES + 1)
            final_url = response.geturl()
    except HTTPError as exc:
        raise WebReadError(f"Web page returned status {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise WebReadError("Could not read that web page.") from exc

    if len(raw) > MAX_PAGE_BYTES:
        raw = raw[:MAX_PAGE_BYTES]

    charset = "utf-8"
    match = re.search(r"charset=([\w.-]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1)
    body = raw.decode(charset, errors="replace")

    if "html" in content_type.lower() or "<html" in body[:1000].lower():
        parser = ReadableHTMLParser()
        parser.feed(body)
        title = parser.title or final_url
        text = parser.text
    else:
        title = final_url
        text = normalize_text(body)

    if not text:
        raise WebReadError("No readable text was found on that page.")

    return {
        "url": final_url,
        "title": title,
        "content": text[:MAX_CONTEXT_CHARS],
        "truncated": len(text) > MAX_CONTEXT_CHARS,
        "source_characters": len(text),
    }
