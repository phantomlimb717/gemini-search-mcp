# /// script
# dependencies = ["google-genai>=1.0", "fastmcp>=2.0", "httpx>=0.27", "trafilatura>=2.0"]
# ///

import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

import httpx
from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gemini-search")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYSTEM = (
    "You are a research analyst. Return plain, unformatted text only. "
    "No Markdown, no headers, no bold, no bullet points. "
    "Use flat paragraphs and simple line breaks only."
)

_BLOCKED_HOSTS = frozenset([
    "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "metadata.google.internal", "169.254.169.254",
])

_BLOCKED_PREFIXES = (
    "10.", "127.", "169.254.",
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "192.168.", "fd", "fe80:", "fc",
)


def _is_private_host(host: str) -> bool:
    if host.lower() in _BLOCKED_HOSTS:
        return True
    if any(host.lower().startswith(p.lower()) for p in _BLOCKED_PREFIXES):
        return True
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return True
    except (socket.gaierror, ValueError):
        pass
    return False


def _clean(text: str) -> str:
    # Strip ALL markdown links -> display text (covers Vertex AI redirect URLs and any other inline links)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Safety net: strip any bare Vertex AI URLs not wrapped in markdown link syntax
    text = re.sub(r'https://vertexaisearch\.cloud\.google\.com/\S+', '', text)
    # Strip bold: **text** or __text__
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_\n]+)__', r'\1', text)
    # Strip ATX headers: ## text -> text
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text.strip()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def research_web(query: str) -> str:
    """Fast grounded web search via Gemini. Use for lookups, fact-checking, current events, and documentation."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

    config = GenerateContentConfig(
        tools=[Tool(google_search=GoogleSearch())],
        system_instruction=SYSTEM,
    )

    response = await client.aio.models.generate_content(
        model=model,
        contents=query,
        config=config,
    )

    lines = [_clean(response.text or "")]

    if response.candidates:
        gm = response.candidates[0].grounding_metadata
        if gm and gm.grounding_chunks:
            lines.append("\nSources:")
            for i, chunk in enumerate(gm.grounding_chunks, 1):
                if hasattr(chunk, "web") and chunk.web:
                    title = chunk.web.title or chunk.web.uri or ""
                    uri = chunk.web.uri or ""
                    lines.append(f"{i}. {title} ({uri})")

    return "\n".join(lines)


@mcp.tool()
async def fetch_webpage(
    url: str,
    max_length: int | None = None,
    start_index: int = 0,
) -> str:
    """
    Fetch and extract plain text content from a webpage.
    Useful for reading articles, docs, or URLs found in research results.
    Supports pagination via start_index and max_length for large pages.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Error: URL must be http/https with a valid hostname."
    if _is_private_host(parsed.hostname):
        return f"Error: SSRF blocked — {parsed.hostname} is a private/internal address."

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; gemini-search/1.0)"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except httpx.TimeoutException:
        return "Error: Request timed out."
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code}"
    except Exception as e:
        return f"Error: {e}"

    # Extract with trafilatura (plain text output)
    content = None
    title = None
    try:
        import trafilatura
        content = trafilatura.extract(
            html,
            url=url,
            output_format="txt",
            include_links=False,
            include_images=False,
            include_tables=True,
            include_comments=False,
        )
        meta = trafilatura.extract_metadata(html)
        if meta:
            title = meta.title
    except Exception:
        pass

    # Fallback: basic HTML parser
    if not content:
        from html.parser import HTMLParser

        class _Extractor(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.parts: list[str] = []
                self.title: str | None = None
                self._in_title = False
                self._skip = 0
                self._SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside"}

            def handle_starttag(self, tag: str, attrs):  # type: ignore[override]
                if tag == "title":
                    self._in_title = True
                elif tag in self._SKIP_TAGS:
                    self._skip += 1

            def handle_endtag(self, tag: str):  # type: ignore[override]
                if tag == "title":
                    self._in_title = False
                elif tag in self._SKIP_TAGS and self._skip:
                    self._skip -= 1
                elif tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li"):
                    self.parts.append("\n")

            def handle_data(self, data: str):  # type: ignore[override]
                text = data.strip()
                if not text:
                    return
                if self._in_title:
                    self.title = text
                elif not self._skip:
                    self.parts.append(text + " ")

        try:
            ex = _Extractor()
            ex.feed(html)
            content = re.sub(r'\n{3,}', '\n\n', "".join(ex.parts).strip())
            title = ex.title
        except Exception:
            content = ""

    # Paginate
    total = len(content or "")
    chunk = content[start_index:]
    truncated = False
    if max_length and len(chunk) > max_length:
        chunk = chunk[:max_length]
        truncated = True

    lines = []
    if title:
        lines.append(title)
        lines.append("")
    lines.append(chunk)
    if truncated:
        next_start = start_index + max_length  # type: ignore[operator]
        lines.append(
            f"\n[Truncated — {total:,} chars total. "
            f"Call again with start_index={next_start} to continue.]"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
