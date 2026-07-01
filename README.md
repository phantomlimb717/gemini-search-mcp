# gemini-search-mcp

A minimal single-file MCP server for grounded web search and webpage fetching via the Gemini API. Built as a lean alternative to `gemini-research-mcp`, optimized for use with Claude Code.

## Why

The maintained `gemini-research-mcp` package ships a lot of machinery (deep research, session persistence, Word export) that is unnecessary if you only want fast grounded search. This is the stripped-down version — two tools, one file, zero state.

## Tools

- **`research_web`** — Grounded web search via Gemini + Google Search. Returns plain text with a sources list.
- **`fetch_webpage`** — Fetches and extracts plain text content from a URL. SSRF-protected, paginated for large pages.

## Requirements

- [uv](https://docs.astral.sh/uv/) — handles all Python dependencies automatically via inline script metadata
- A Gemini API key

## Setup

### 1. Environment variables

```bash
export GEMINI_API_KEY="your-api-key"
export GEMINI_MODEL="gemini-2.0-flash"   # optional, this is the default
```

### 2. Register with Claude Code

Add to `~/.claude.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "gemini-search": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "/path/to/gemini_search.py"],
      "env": {}
    }
  }
}
```

### 3. Add routing instruction to `~/.claude/CLAUDE.md`

```
- ALWAYS route all web searches, documentation lookups, and API references
  through the gemini-search MCP server using the research_web tool.
```

### 4. Test

```bash
uv run gemini_search.py
```

`uv` installs the four dependencies on first run (~20s), then the server starts and waits for connections. If it hangs without errors, it's working. `Ctrl+C` to stop.

## Output

Both tools return plain, unformatted text — no Markdown, no bold, no headers, no Vertex AI redirect URLs. Designed for consumption by LLMs rather than human rendering.

## Dependencies

Declared inline via [PEP 723](https://peps.python.org/pep-0723/) script metadata — no `pyproject.toml` or manual installs needed:

- `google-genai >= 1.0`
- `fastmcp >= 2.0`
- `httpx >= 0.27`
- `trafilatura >= 2.0`
