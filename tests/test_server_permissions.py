"""Integration tests for tool execution with permissions."""

import pytest

from ankimcp.permissions import PermissionManager
from ankimcp.tools import ToolExecutor
from tests.test_utils import MockAnkiInterface


class TestServerPermissions:
    """Test tool execution with permission enforcement."""

    @pytest.fixture
    def restricted_anki(self):
        """Create an AnkiInterface with restricted permissions."""
        anki = MockAnkiInterface()
        anki.permissions = PermissionManager(
            {
                "permissions": {
                    "global": {"read": True, "write": False, "delete": False},
                    "mode": "allowlist",
                    "deck_permissions": {
                        "allowlist": ["Spanish", "Languages::*"],
                    },
                    "protected_decks": ["Spanish"],
                    "tag_restrictions": {
                        "protected_tags": ["exam"],
                    },
                }
            }
        )

        anki.decks[3] = {
            "id": 3,
            "name": "Languages::French",
            "card_count": 20,
            "is_filtered": False,
        }

        return anki

    @pytest.mark.asyncio
    async def test_get_permissions_tool(self, restricted_anki):
        """Test get_permissions tool."""
        executor = ToolExecutor(restricted_anki)
        result = await executor.execute("get_permissions", {})
        assert len(result) == 1

        text = result[0].text
        assert "allowlist" in text
        assert "Spanish" in text
        assert "read': True" in text
        assert "write': False" in text

    @pytest.mark.asyncio
    async def test_list_decks_filtered(self, restricted_anki):
        """Test list_decks returns only allowed decks."""
        executor = ToolExecutor(restricted_anki)
        result = await executor.execute("list_decks", {})
        assert len(result) == 1

        decks_text = result[0].text
        assert "Spanish" in decks_text
        assert "Languages::French" in decks_text
        assert "Default" not in decks_text  # Not in allowlist

    @pytest.mark.asyncio
    async def test_get_deck_info_denied(self, restricted_anki):
        """Test get_deck_info with permission denied."""
        executor = ToolExecutor(restricted_anki)

        # Allowed deck should work
        result = await executor.execute("get_deck_info", {"deck_name": "Spanish"})
        assert "Spanish" in result[0].text

        # Denied deck should return error
        result = await executor.execute("get_deck_info", {"deck_name": "Default"})
        assert "Error:" in result[0].text
        assert "not in the allowlist" in result[0].text

    @pytest.mark.asyncio
    async def test_create_note_denied(self, restricted_anki):
        """Test create_note with write permission denied."""
        executor = ToolExecutor(restricted_anki)
        result = await executor.execute(
            "create_note",
            {
                "model_name": "Basic",
                "fields": {"Front": "Test", "Back": "Prueba"},
                "deck_name": "Spanish",
            },
        )
        assert "Error:" in result[0].text
        assert "Global write permission denied" in result[0].text

    @pytest.mark.asyncio
    async def test_update_note_denied(self, restricted_anki):
        """Test update_note with write permission denied."""
        executor = ToolExecutor(restricted_anki)
        result = await executor.execute(
            "update_note",
            {
                "note_id": 1,
                "fields": {"Front": "Updated"},
            },
        )
        assert "Error:" in result[0].text
        assert "Global write permission denied" in result[0].text

    @pytest.mark.asyncio
    async def test_delete_note_denied(self, restricted_anki):
        """Test delete_note with delete permission denied."""
        executor = ToolExecutor(restricted_anki)
        result = await executor.execute("delete_note", {"note_id": 1})
        assert "Error:" in result[0].text
        assert "Global delete permission denied" in result[0].text

    @pytest.mark.asyncio
    async def test_search_notes_filtered(self):
        """Test search_notes with tag filtering."""
        filtered_anki = MockAnkiInterface()
        filtered_anki.permissions = PermissionManager(
            {
                "permissions": {
                    "global": {"read": True, "write": True, "delete": True},
                    "mode": "denylist",
                    "tag_restrictions": {
                        "protected_tags": ["private"],
                    },
                }
            }
        )

        filtered_anki.notes = {
            1: {
                "id": 1,
                "model_name": "Basic",
                "fields": {"Front": "Public", "Back": "Público"},
                "tags": ["spanish"],
                "card_count": 1,
            },
            2: {
                "id": 2,
                "model_name": "Basic",
                "fields": {"Front": "Private", "Back": "Privado"},
                "tags": ["spanish", "private"],
                "card_count": 1,
            },
        }

        executor = ToolExecutor(filtered_anki)
        result = await executor.execute("search_notes", {"query": "spanish"})
        notes_text = result[0].text
        assert "Public" in notes_text
        assert "Private" in notes_text

    @pytest.mark.asyncio
    async def test_create_deck_in_protected(self):
        """Test creating deck when parent is protected."""
        protected_anki = MockAnkiInterface()
        protected_anki.permissions = PermissionManager(
            {
                "permissions": {
                    "global": {"read": True, "write": True, "delete": True},
                    "mode": "denylist",
                    "protected_decks": ["Languages"],
                }
            }
        )

        executor = ToolExecutor(protected_anki)
        result = await executor.execute("create_deck", {"deck_name": "Languages"})
        assert "Error:" in result[0].text
        assert "protected" in result[0].text

    @pytest.mark.asyncio
    async def test_note_type_restriction(self):
        """Test note type restrictions in server."""
        type_restricted_anki = MockAnkiInterface()
        type_restricted_anki.permissions = PermissionManager(
            {
                "permissions": {
                    "global": {"read": True, "write": True, "delete": True},
                    "mode": "denylist",
                    "deck_permissions": {"allowlist": [], "denylist": []},
                    "protected_decks": [],
                    "note_type_permissions": {
                        "allow_create": False,
                        "allowed_types": ["Basic"],
                    },
                }
            }
        )

        executor = ToolExecutor(type_restricted_anki)

        # Can't create new note types
        result = await executor.execute(
            "create_note_type",
            {
                "name": "Custom",
                "fields": ["Q", "A"],
                "templates": [{"name": "Card 1", "qfmt": "{{Q}}", "afmt": "{{A}}"}],
            },
        )
        assert "Error:" in result[0].text
        assert "Creating new note types is not allowed" in result[0].text

        # Can use allowed note type
        result = await executor.execute(
            "create_note",
            {
                "model_name": "Basic",
                "fields": {"Front": "Test", "Back": "Test"},
                "deck_name": "Default",
            },
        )
        assert "Error:" not in result[0].text
