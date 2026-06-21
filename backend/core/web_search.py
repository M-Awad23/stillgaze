from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from backend.core.llm import ChatMessage
from backend.core.web_reader import WebReadError, read_web_page, validate_url


MAX_SEARCH_RESULTS = 5
MAX_WEB_SOURCES = 3
MAX_WEB_CONTEXT_CHARS = 9_000
WEB_INTENT_WORDS = {
    "current",
    "internet",
    "latest",
    "news",
    "online",
    "recent",
    "resource",
    "resources",
    "search",
    "source",
    "sources",
    "today",
    "web",
    "website",
}
WEB_INTENT_PHRASES = (
    "browse",
    "cite",
    "find me",
    "from the web",
    "go online",
    "look up",
    "pull resources",
    "search for",
)
URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)


@dataclass(frozen=True)
class WebSource:
    title: str
    url: str
    content: str
    truncated: bool


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return

        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")
        href = attrs_dict.get("href")
        if not href or "result__a" not in class_name:
            return

        url = unwrap_duckduckgo_url(href)
        if url and url not in self.urls:
            self.urls.append(url)


def should_use_web(messages: list[ChatMessage]) -> bool:
    latest_user = next((message.content for message in reversed(messages) if message.role == "user"), "")
    if not latest_user:
        return False
    normalized = latest_user.lower()
    words = set(re.findall(r"[a-z]+", normalized))
    return bool(
        URL_RE.search(latest_user)
        or words.intersection(WEB_INTENT_WORDS)
        or any(phrase in normalized for phrase in WEB_INTENT_PHRASES)
    )


def build_web_augmented_messages(messages: list[ChatMessage]) -> tuple[list[ChatMessage], list[WebSource]]:
    latest_user = next((message.content for message in reversed(messages) if message.role == "user"), "")
    sources = collect_web_sources(latest_user)
    if not sources:
        return (
            [
                ChatMessage(
                    role="system",
                    content=(
                        "The user asked for web access, but StillGaze could not retrieve web sources. "
                        "Be transparent about that instead of inventing current information."
                    ),
                ),
                *messages,
            ],
            [],
        )

    context_parts = [
        "Use the following live web sources when they are relevant.",
        "Cite source titles or URLs in the answer. Do not invent sources.",
    ]
    used_chars = 0
    for index, source in enumerate(sources, start=1):
        remaining = MAX_WEB_CONTEXT_CHARS - used_chars
        if remaining <= 0:
            break
        content = source.content[:remaining]
        used_chars += len(content)
        context_parts.extend(
            [
                "",
                f"[{index}] {source.title}",
                f"URL: {source.url}",
                "Content:",
                content,
            ]
        )

    return [ChatMessage(role="system", content="\n".join(context_parts)), *messages], sources


def collect_web_sources(query: str) -> list[WebSource]:
    urls = extract_urls(query)
    if not urls:
        urls = search_web(query)

    sources: list[WebSource] = []
    for url in urls:
        if len(sources) >= MAX_WEB_SOURCES:
            break
        try:
            page = read_web_page(url)
        except WebReadError:
            continue
        sources.append(
            WebSource(
                title=str(page["title"]),
                url=str(page["url"]),
                content=str(page["content"]),
                truncated=bool(page["truncated"]),
            )
        )
    return sources


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.findall(text):
        url = match.rstrip(".,;:!?)]}")
        try:
            urls.append(validate_url(url))
        except WebReadError:
            continue
    return list(dict.fromkeys(urls))


def search_web(query: str) -> list[str]:
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    request = Request(
        search_url,
        headers={
            "User-Agent": "StillGaze/0.1 (+local web search)",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=12) as response:
            body = response.read(900_000).decode("utf-8", errors="replace")
    except OSError:
        return []

    parser = DuckDuckGoHTMLParser()
    parser.feed(body)
    return parser.urls[:MAX_SEARCH_RESULTS]


def unwrap_duckduckgo_url(href: str) -> str:
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return href
    return ""
