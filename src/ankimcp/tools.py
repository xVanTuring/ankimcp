"""Tool definitions for AnkiMCP."""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from .anki_interface import AnkiInterface

logger = logging.getLogger(__name__)


def _to_json(result: Any) -> str:
    """Serialize a tool result as JSON so clients can parse it.

    ensure_ascii is off to keep non-Latin fields readable and compact; default=str
    covers the odd Anki object that is not JSON-native.
    """
    return json.dumps(result, ensure_ascii=False, default=str)


@dataclass
class Tool:
    """Lightweight replacement for mcp.types.Tool to avoid pydantic_core dependency."""

    name: str
    description: str
    inputSchema: Dict[str, Any]


@dataclass
class ToolResult:
    """Result of a tool execution. Lightweight replacement for mcp.types.TextContent."""

    text: str
    is_error: bool = False


# Define all available tools in one place
AVAILABLE_TOOLS = [
    Tool(
        name="get_permissions",
        description="Get current permission settings and status",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="list_decks",
        description="List all available Anki decks",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_deck_info",
        description="Get detailed information about a specific deck",
        inputSchema={
            "type": "object",
            "properties": {
                "deck_name": {
                    "type": "string",
                    "description": "Name of the deck to get info for",
                }
            },
            "required": ["deck_name"],
        },
    ),
    Tool(
        name="search_notes",
        description="Search for notes using Anki's search syntax",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Anki search query",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results",
                    "default": 50,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="search_card_states",
        description=(
            "Search cards and return their scheduling state (new/learning/relearning/"
            "young/mature) plus only the note fields you ask for, with HTML stripped. "
            "Unlike search_notes this never returns whole notes, so it is the right "
            "tool for bulk-exporting the study state of a large deck."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Anki search query, e.g. 'deck:Spanish -is:new'",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Note field names to include, e.g. ['Front']. "
                        "Omit for state only. Names not on the note are skipped."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of cards; 0 means no limit",
                    "default": 0,
                },
                "strip_html": {
                    "type": "boolean",
                    "description": "Strip HTML markup from returned field values",
                    "default": True,
                },
                "by_note": {
                    "type": "boolean",
                    "description": (
                        "Merge cards that share a note into one entry per note "
                        "(returns 'notes' instead of 'cards'). Useful when note "
                        "types have multiple card templates."
                    ),
                    "default": False,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_note",
        description="Get detailed information about a specific note",
        inputSchema={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "integer",
                    "description": "ID of the note to retrieve",
                }
            },
            "required": ["note_id"],
        },
    ),
    Tool(
        name="get_cards_for_note",
        description="Get all cards associated with a specific note",
        inputSchema={
            "type": "object",
            "properties": {
                "note_id": {"type": "integer", "description": "ID of the note"}
            },
            "required": ["note_id"],
        },
    ),
    Tool(
        name="get_review_stats",
        description="Get review statistics for a deck or overall",
        inputSchema={
            "type": "object",
            "properties": {
                "deck_name": {
                    "type": "string",
                    "description": "Name of the deck (optional)",
                }
            },
            "required": [],
        },
    ),
    Tool(
        name="list_note_types",
        description="List all available note types (models) with their fields and templates",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="create_deck",
        description="Create a new deck",
        inputSchema={
            "type": "object",
            "properties": {
                "deck_name": {
                    "type": "string",
                    "description": "Name of the deck to create",
                }
            },
            "required": ["deck_name"],
        },
    ),
    Tool(
        name="create_note_type",
        description="Create a new note type (model)",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the note type",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of field names",
                },
                "templates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "qfmt": {"type": "string"},
                            "afmt": {"type": "string"},
                        },
                        "required": ["name", "qfmt", "afmt"],
                    },
                    "description": "List of card templates",
                },
            },
            "required": ["name", "fields", "templates"],
        },
    ),
    Tool(
        name="create_note",
        description="Create a new note",
        inputSchema={
            "type": "object",
            "properties": {
                "model_name": {
                    "type": "string",
                    "description": "Name of the note type (model)",
                },
                "fields": {
                    "type": "object",
                    "description": "Field name to value mapping",
                },
                "deck_name": {
                    "type": "string",
                    "description": "Name of the deck to add the note to",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of tags",
                },
            },
            "required": ["model_name", "fields", "deck_name"],
        },
    ),
    Tool(
        name="update_note",
        description=(
            "Update an existing note. Returns a minimal confirmation by default; "
            "use return_fields to read back specific fields after the update. "
            "Field names must match the note type exactly (case and whitespace "
            "sensitive); unknown field names raise an error."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "integer",
                    "description": "ID of the note to update",
                },
                "fields": {
                    "type": "object",
                    "description": "Field name to value mapping (only fields to update)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New list of tags (replaces existing tags)",
                },
                "return_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Field names to include in the response, e.g. ['Front']. "
                        "Omit for a minimal confirmation; ['*'] returns all fields."
                    ),
                },
                "strip_html": {
                    "type": "boolean",
                    "description": (
                        "Strip HTML markup from returned field values "
                        "(only applies with return_fields)"
                    ),
                    "default": False,
                },
            },
            "required": ["note_id"],
        },
    ),
    Tool(
        name="update_notes",
        description=(
            "Update multiple notes in one call. Each update is applied "
            "independently: per-note failures are reported in the results list "
            "without aborting the rest of the batch."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "note_id": {"type": "integer"},
                            "fields": {"type": "object"},
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["note_id"],
                    },
                    "description": (
                        "List of updates, each {note_id, fields?, tags?} — "
                        "same semantics as update_note."
                    ),
                },
                "return_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Field names to include in each result, e.g. ['Front']. "
                        "Omit for minimal confirmations; ['*'] returns all fields."
                    ),
                },
                "strip_html": {
                    "type": "boolean",
                    "description": (
                        "Strip HTML markup from returned field values "
                        "(only applies with return_fields)"
                    ),
                    "default": False,
                },
            },
            "required": ["updates"],
        },
    ),
    Tool(
        name="delete_note",
        description="Delete a note and all its cards",
        inputSchema={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "integer",
                    "description": "ID of the note to delete",
                }
            },
            "required": ["note_id"],
        },
    ),
    Tool(
        name="delete_deck",
        description="Delete a deck and all its cards. Cannot delete protected decks.",
        inputSchema={
            "type": "object",
            "properties": {
                "deck_name": {
                    "type": "string",
                    "description": "Name of the deck to delete",
                }
            },
            "required": ["deck_name"],
        },
    ),
    Tool(
        name="update_deck",
        description="Update a deck's properties (name, description). Cannot update protected decks.",
        inputSchema={
            "type": "object",
            "properties": {
                "deck_name": {
                    "type": "string",
                    "description": "Current name of the deck to update",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name for the deck (optional)",
                },
                "description": {
                    "type": "string",
                    "description": "New description for the deck (optional)",
                },
            },
            "required": ["deck_name"],
        },
    ),
    Tool(
        name="sync",
        description="Sync Anki collection with AnkiWeb",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
]


def get_tool_schemas():
    """Get tool schemas for HTTP responses."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema,
        }
        for tool in AVAILABLE_TOOLS
    ]


class ToolExecutor:
    """Executes MCP tools against an AnkiInterface.

    Single responsibility: dispatch tool calls and return ToolResult.
    Depends on AnkiInterface abstraction, not on any transport layer.
    """

    def __init__(self, anki: "AnkiInterface") -> None:
        self.anki = anki

    async def execute(self, name: str, arguments: Dict[str, Any]) -> List[ToolResult]:
        """Execute a named tool with given arguments."""
        try:
            result = await self._dispatch(name, arguments)
            return [ToolResult(text=_to_json(result))]
        except Exception as e:
            logger.error(f"Error executing tool {name}: {e}")
            return [ToolResult(text=f"Error: {str(e)}", is_error=True)]

    async def _dispatch(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Route tool name to the appropriate AnkiInterface method."""
        handlers: Dict[str, Any] = {
            "get_permissions": lambda: self.anki.permissions.get_permission_summary(),
            "list_decks": lambda: self.anki.list_decks(),
            "get_deck_info": lambda: self.anki.get_deck_info(arguments["deck_name"]),
            "search_notes": lambda: self.anki.search_notes(
                arguments["query"], limit=arguments.get("limit", 50)
            ),
            "search_card_states": lambda: self.anki.search_card_states(
                arguments["query"],
                fields=arguments.get("fields"),
                limit=arguments.get("limit", 0),
                strip_html=arguments.get("strip_html", True),
                by_note=arguments.get("by_note", False),
            ),
            "get_note": lambda: self.anki.get_note(arguments["note_id"]),
            "get_cards_for_note": lambda: self.anki.get_cards_for_note(
                arguments["note_id"]
            ),
            "get_review_stats": lambda: self.anki.get_review_stats(
                arguments.get("deck_name")
            ),
            "list_note_types": lambda: self.anki.list_note_types(),
            "create_deck": lambda: self.anki.create_deck(arguments["deck_name"]),
            "create_note_type": lambda: self.anki.create_note_type(
                arguments["name"], arguments["fields"], arguments["templates"]
            ),
            "create_note": lambda: self.anki.create_note(
                arguments["model_name"],
                arguments["fields"],
                arguments["deck_name"],
                tags=arguments.get("tags"),
            ),
            "update_note": lambda: self.anki.update_note(
                arguments["note_id"],
                fields=arguments.get("fields"),
                tags=arguments.get("tags"),
                return_fields=arguments.get("return_fields"),
                strip_html=arguments.get("strip_html", False),
            ),
            "update_notes": lambda: self.anki.update_notes(
                arguments["updates"],
                return_fields=arguments.get("return_fields"),
                strip_html=arguments.get("strip_html", False),
            ),
            "delete_note": lambda: self.anki.delete_note(arguments["note_id"]),
            "delete_deck": lambda: self.anki.delete_deck(arguments["deck_name"]),
            "update_deck": lambda: self.anki.update_deck(
                arguments["deck_name"],
                arguments.get("new_name"),
                arguments.get("description"),
            ),
            "sync": lambda: self.anki.sync(),
        }

        handler = handlers.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")

        result = handler()
        if asyncio.iscoroutine(result):
            result = await result
        return result
