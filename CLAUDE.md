# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

AnkiMCP is an Anki addon that exposes Anki collection data via a Model Context Protocol (MCP) server. This allows AI assistants to interact with Anki decks, notes, cards, and review statistics.

## Development Environment

- **Python Version**: 3.12.9 (specified in `.python-version`)
- **Package Manager**: Rye
- **Build System**: Hatchling
- **Package Structure**: Uses modern `src/` layout
- **Key Dependencies**: `mcp` (MCP SDK), `anki` (Anki Python library)

## Common Commands

### Dependency Management
```bash
# Install dependencies
rye sync

# Add a new dependency
rye add <package>

# Add a development dependency
rye add --dev <package>

# Update dependencies
rye lock --update-all
```

### Development
```bash
# Run the test server with mock data
rye run python -m ankimcp

# Install in editable mode for development
rye sync
```

### Testing
```bash
# Run tests (after test framework is added)
rye run pytest

# Run tests with coverage
rye run pytest --cov=ankimcp
```

### Linting and Formatting
```bash
# Format code
rye run black src/

# Lint code
rye run ruff check src/

# Type checking
rye run mypy src/
```

## Project Structure

```
src/ankimcp/
├── __init__.py          # Anki addon entry point with hooks
├── __main__.py          # Standalone usage info
├── server.py            # MCP server implementation (SDK-based)
├── simple_http_server.py # HTTP server with SSE transport (runs in Anki)
├── anki_interface.py    # Abstraction layer for Anki operations
├── tools.py             # MCP tool definitions
├── permissions.py       # Permission management system
├── config.json          # Anki addon default configuration
├── config.md            # Configuration documentation (shown in Anki)
└── manifest.json        # Anki addon metadata
```

## Architecture

The addon follows a layered architecture:

1. **Anki Integration Layer** (`__init__.py`)
   - Registers Anki hooks to start/stop the MCP server
   - Manages server lifecycle with profile open/close

2. **MCP Server** (`server.py`)
   - Implements MCP protocol endpoints
   - Defines available tools for AI interaction
   - Handles tool execution and response formatting

3. **Anki Interface** (`anki_interface.py`)
   - Provides abstraction over Anki's API
   - Implements data access methods
   - Handles conversion between Anki objects and JSON

4. **Test Infrastructure** (`__main__.py`)
   - Mock implementation for testing without Anki
   - Allows standalone server testing

## Available MCP Tools

**Read operations:**
- `list_decks`: Returns all decks with card counts
- `get_deck_info`: Detailed deck statistics
- `search_notes`: Search using Anki's query syntax (returns *whole* notes — expensive on rich note types)
- `search_card_states`: Search cards, returning scheduling state + only the requested fields, HTML stripped. Use this for bulk export; `search_notes` on a few thousand rich notes is tens of MB. Pass `by_note: true` to merge cards that share a note into one entry per note.
- `get_note`: Full note data with fields
- `get_cards_for_note`: All cards for a specific note
- `get_review_stats`: Review statistics for deck or collection
- `list_note_types`: All note types with fields and templates
- `get_permissions`: Current permission settings

**Write operations:**
- `create_deck`: Create a new deck
- `create_note_type`: Create a new note type
- `create_note`: Create a new note
- `update_note`: Update note fields or tags. Returns a minimal confirmation by default; pass `return_fields` (e.g. `["Front"]`, or `["*"]` for all) to read fields back — avoids echoing whole notes with long HTML fields. Returned values are raw unless `strip_html: true`.
- `update_notes`: Batch version of `update_note` — one call applies a list of `{note_id, fields?, tags?}` updates; per-note failures are reported in the results list without aborting the batch.
- `update_deck`: Update deck name or description
- `delete_note`: Delete a note and its cards
- `delete_deck`: Delete a deck and its cards

## Installation as Anki Addon

To install in Anki:
1. Copy `src/ankimcp/` to Anki's addons directory
2. Restart Anki
3. Server starts automatically on profile load

## Key Implementation Details

- HTTP server with Streamable HTTP transport runs on port 4473 (configurable)
- Threaded server handles concurrent connections
- MCP clients connect via the Streamable HTTP endpoint (`/mcp`)
- Tool results are serialized with `json.dumps` (`ensure_ascii=False`), so clients can parse them directly. Error results stay as plain `Error: ...` text.
- Gracefully handles Anki availability (addon vs standalone)
- Configuration via Anki's addon config system
- Comprehensive permission system for access control
