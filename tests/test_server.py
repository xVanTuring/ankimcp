"""Tests for the MCP tool definitions."""

from ankimcp.tools import AVAILABLE_TOOLS


def test_list_tools():
    """Test that AVAILABLE_TOOLS contains expected tools."""
    assert len(AVAILABLE_TOOLS) == 15

    tool_names = {tool.name for tool in AVAILABLE_TOOLS}
    expected_names = {
        "get_permissions",
        "list_decks",
        "get_deck_info",
        "search_notes",
        "get_note",
        "get_cards_for_note",
        "get_review_stats",
        "list_note_types",
        "create_deck",
        "create_note_type",
        "create_note",
        "update_note",
        "delete_note",
        "delete_deck",
        "update_deck",
    }
    assert tool_names == expected_names

    for tool in AVAILABLE_TOOLS:
        assert tool.name
        assert tool.description
        assert tool.inputSchema
