"""Tests for the Anki interface."""

import pytest

from ankimcp.anki_interface import _card_state, _plain_text
from tests.test_utils import MockAnkiInterface


@pytest.fixture
def mock_anki():
    """Provide a mock Anki interface."""
    return MockAnkiInterface()


@pytest.mark.asyncio
async def test_list_decks(mock_anki):
    """Test listing decks."""
    decks = await mock_anki.list_decks()
    assert len(decks) == 2
    assert decks[0]["name"] == "Default"
    assert decks[1]["name"] == "Spanish"


@pytest.mark.asyncio
async def test_get_deck_info(mock_anki):
    """Test getting deck info."""
    info = await mock_anki.get_deck_info("Default")
    assert info["name"] == "Default"
    assert info["card_count"] == 10
    assert info["new_count"] == 3

    # Test non-existent deck
    with pytest.raises(ValueError):
        await mock_anki.get_deck_info("NonExistent")


@pytest.mark.asyncio
async def test_search_notes(mock_anki):
    """Test searching notes."""
    notes = await mock_anki.search_notes("tag:spanish")
    assert len(notes) == 1
    assert notes[0]["fields"]["Front"] == "Hello"


@pytest.mark.asyncio
async def test_search_card_states(mock_anki):
    """Test the compact card-state search."""
    result = await mock_anki.search_card_states("tag:spanish", fields=["Front"])

    assert result["count"] == 1
    assert result["matched"] == 1
    assert result["truncated"] is False

    card = result["cards"][0]
    assert card["state"] == "mature"  # ivl 30 >= 21
    assert card["ivl"] == 30
    assert card["deck"] == "Spanish"
    assert card["fields"] == {"Front": "Hello"}


@pytest.mark.asyncio
async def test_search_card_states_omits_fields_by_default(mock_anki):
    """Fields are only returned when explicitly requested."""
    result = await mock_anki.search_card_states("tag:spanish")
    assert "fields" not in result["cards"][0]


@pytest.mark.asyncio
async def test_search_card_states_skips_unknown_fields(mock_anki):
    """Unknown field names are skipped rather than raising."""
    result = await mock_anki.search_card_states(
        "tag:spanish", fields=["Front", "NotAField"]
    )
    assert result["cards"][0]["fields"] == {"Front": "Hello"}


@pytest.mark.asyncio
async def test_search_card_states_truncates(mock_anki):
    """A limit smaller than the match count reports truncation."""
    mock_anki.cards[2] = dict(mock_anki.cards[1], cid=2)

    result = await mock_anki.search_card_states("tag:spanish", limit=1)
    assert result["count"] == 1
    assert result["matched"] == 2
    assert result["truncated"] is True


def test_card_state_mapping():
    """Card type + interval map onto the expected state labels."""
    assert _card_state(0, 0) == "new"
    assert _card_state(1, 0) == "learning"
    assert _card_state(3, 5) == "relearning"
    assert _card_state(2, 20) == "young"
    assert _card_state(2, 21) == "mature"


def test_plain_text_strips_markup():
    """HTML is stripped the way Anki does it: no whitespace inserted."""
    assert _plain_text("con<b></b>sole") == "console"
    assert _plain_text("<div>a</div>") == "a"
    assert _plain_text("<!-- note -->hi") == "hi"
    assert _plain_text("<style>.x{color:red}</style>word") == "word"
    assert _plain_text("a&nbsp;&amp;&nbsp;b") == "a & b"
    assert _plain_text("  spaced   out  ") == "spaced out"


@pytest.mark.asyncio
async def test_get_note(mock_anki):
    """Test getting a specific note."""
    note = await mock_anki.get_note(1)
    assert note["id"] == 1
    assert note["model_name"] == "Basic"

    # Test non-existent note
    with pytest.raises(ValueError):
        await mock_anki.get_note(999)


@pytest.mark.asyncio
async def test_get_cards_for_note(mock_anki):
    """Test getting cards for a note."""
    cards = await mock_anki.get_cards_for_note(1)
    assert len(cards) == 1
    assert cards[0]["note_id"] == 1
    assert cards[0]["deck_name"] == "Spanish"


@pytest.mark.asyncio
async def test_get_review_stats(mock_anki):
    """Test getting review statistics."""
    stats = await mock_anki.get_review_stats()
    assert stats["deck_name"] == "All Decks"
    assert stats["total_cards"] == 60

    stats = await mock_anki.get_review_stats("Default")
    assert stats["deck_name"] == "Default"


@pytest.mark.asyncio
async def test_create_deck(mock_anki):
    """Test creating a new deck."""
    # Create a new deck
    result = await mock_anki.create_deck("Japanese")
    assert result["name"] == "Japanese"
    assert result["created"] is True
    assert "id" in result

    # Verify deck was created
    decks = await mock_anki.list_decks()
    deck_names = [d["name"] for d in decks]
    assert "Japanese" in deck_names

    # Try to create the same deck again
    result = await mock_anki.create_deck("Japanese")
    assert result["created"] is False
    assert result["name"] == "Japanese"


@pytest.mark.asyncio
async def test_create_note_type(mock_anki):
    """Test creating a new note type."""
    # Create a new note type
    fields = ["Question", "Answer", "Extra"]
    templates = [
        {
            "name": "Forward",
            "qfmt": "{{Question}}",
            "afmt": "{{FrontSide}}<hr id=answer>{{Answer}}<br>{{Extra}}",
        },
        {
            "name": "Reverse",
            "qfmt": "{{Answer}}",
            "afmt": "{{FrontSide}}<hr id=answer>{{Question}}<br>{{Extra}}",
        },
    ]

    result = await mock_anki.create_note_type("Custom", fields, templates)
    assert result["name"] == "Custom"
    assert result["field_count"] == 3
    assert result["template_count"] == 2
    assert result["created"] is True
    assert "id" in result

    # Try to create the same note type again
    with pytest.raises(ValueError, match="Model already exists"):
        await mock_anki.create_note_type("Custom", fields, templates)


@pytest.mark.asyncio
async def test_create_note(mock_anki):
    """Test creating a new note."""
    # Create a note with Basic model in Spanish deck
    fields = {"Front": "Goodbye", "Back": "Adiós"}
    tags = ["spanish", "common"]

    result = await mock_anki.create_note(
        model_name="Basic", fields=fields, deck_name="Spanish", tags=tags
    )

    assert result["model_name"] == "Basic"
    assert result["fields"] == fields
    assert result["tags"] == tags
    assert "id" in result
    assert result["card_count"] == 1

    # Verify note was created
    note = await mock_anki.get_note(result["id"])
    assert note["fields"]["Front"] == "Goodbye"
    assert note["fields"]["Back"] == "Adiós"
    assert "spanish" in note["tags"]

    # Test with non-existent model
    with pytest.raises(ValueError, match="Model not found"):
        await mock_anki.create_note(
            model_name="NonExistent", fields=fields, deck_name="Spanish"
        )

    # Test with non-existent deck
    with pytest.raises(ValueError, match="Deck not found"):
        await mock_anki.create_note(
            model_name="Basic", fields=fields, deck_name="NonExistent"
        )


@pytest.mark.asyncio
async def test_update_note(mock_anki):
    """Updating a note returns a minimal confirmation by default."""
    note = await mock_anki.get_note(1)
    original_tags = note["tags"]

    # Default response is a minimal confirmation
    result = await mock_anki.update_note(
        1, fields={"Front": "Hi", "Back": "Hola (updated)"}
    )
    assert result == {
        "note_id": 1,
        "updated_fields": ["Front", "Back"],
        "tags_updated": False,
        "success": True,
    }

    # The fields were actually updated; tags untouched
    note = await mock_anki.get_note(1)
    assert note["fields"]["Front"] == "Hi"
    assert note["fields"]["Back"] == "Hola (updated)"
    assert note["tags"] == original_tags

    # return_fields reads back only the requested fields
    result = await mock_anki.update_note(
        1, fields={"Front": "Hello again"}, return_fields=["Front"]
    )
    assert result["fields"] == {"Front": "Hello again"}
    assert result["updated_fields"] == ["Front"]

    # "*" reads back all fields
    result = await mock_anki.update_note(1, fields={"Front": "Hi"}, return_fields=["*"])
    assert result["fields"] == {"Front": "Hi", "Back": "Hola (updated)"}

    # Tag updates are confirmed inline
    new_tags = ["spanish", "greetings", "updated"]
    result = await mock_anki.update_note(1, tags=new_tags)
    assert result["tags_updated"] is True
    assert result["tags"] == new_tags
    assert result["updated_fields"] == []

    # Test updating non-existent note
    with pytest.raises(ValueError, match="Note not found"):
        await mock_anki.update_note(999, fields={"Front": "Test"})


@pytest.mark.asyncio
async def test_update_notes(mock_anki):
    """Batch update applies each item independently and reports failures."""
    result = await mock_anki.update_notes(
        [
            {"note_id": 1, "fields": {"Front": "Batch"}, "tags": ["spanish"]},
            {"note_id": 999, "fields": {"Front": "Nope"}},
            {"fields": {"Front": "Missing id"}},
        ]
    )

    assert result["count"] == 3
    assert result["succeeded"] == 1
    assert result["failed"] == 2

    first = result["results"][0]
    assert first["success"] is True
    assert first["note_id"] == 1
    assert first["updated_fields"] == ["Front"]

    assert result["results"][1]["success"] is False
    assert result["results"][1]["note_id"] == 999
    assert "error" in result["results"][1]

    assert result["results"][2]["success"] is False
    assert result["results"][2]["note_id"] is None

    # The successful update was applied
    note = await mock_anki.get_note(1)
    assert note["fields"]["Front"] == "Batch"

    # return_fields applies to each result
    result = await mock_anki.update_notes(
        [{"note_id": 1, "fields": {"Back": "Adiós"}}], return_fields=["Back"]
    )
    assert result["results"][0]["fields"] == {"Back": "Adiós"}

    # strip_html applies to returned field values
    result = await mock_anki.update_notes(
        [{"note_id": 1, "fields": {"Back": "<b>Adiós</b>"}}],
        return_fields=["Back"],
        strip_html=True,
    )
    assert result["results"][0]["fields"] == {"Back": "Adiós"}

    # Raw values are returned by default
    result = await mock_anki.update_note(
        1, fields={"Back": "<b>Adiós</b>"}, return_fields=["Back"]
    )
    assert result["fields"] == {"Back": "<b>Adiós</b>"}


@pytest.mark.asyncio
async def test_search_card_states_by_note(mock_anki):
    """by_note merges cards that share a note into one entry."""
    mock_anki.cards[2] = dict(mock_anki.cards[1], cid=2, ivl=5)

    result = await mock_anki.search_card_states(
        "tag:spanish", fields=["Front"], by_note=True
    )

    assert result["count"] == 1  # one note, not two cards
    assert result["matched"] == 2
    assert "cards" not in result

    note = result["notes"][0]
    assert note["nid"] == 1
    assert note["fields"] == {"Front": "Hello"}  # fields hoisted to the note
    assert len(note["cards"]) == 2
    assert all("nid" not in card for card in note["cards"])
    assert note["cards"][0]["state"] == "mature"
    assert note["cards"][1]["state"] == "young"


@pytest.mark.asyncio
async def test_delete_note(mock_anki):
    """Test deleting a note."""
    # First create a new note to delete
    fields = {"Front": "Test", "Back": "Prueba"}
    created_note = await mock_anki.create_note(
        model_name="Basic", fields=fields, deck_name="Spanish", tags=["test"]
    )
    note_id = created_note["id"]

    # Verify the note exists
    note = await mock_anki.get_note(note_id)
    assert note["fields"]["Front"] == "Test"

    # Delete the note
    result = await mock_anki.delete_note(note_id)
    assert result["note_id"] == note_id
    assert result["deleted"] is True
    assert result["cards_deleted"] == 1

    # Verify the note no longer exists
    with pytest.raises(ValueError, match="Note not found"):
        await mock_anki.get_note(note_id)

    # Test deleting non-existent note
    with pytest.raises(ValueError, match="Note not found"):
        await mock_anki.delete_note(999)


@pytest.mark.asyncio
async def test_mutation_integration(mock_anki):
    """Test a complete workflow with multiple mutations."""
    # Create a new deck
    deck_result = await mock_anki.create_deck("Integration Test")
    assert deck_result["created"] is True

    # Create a new note type
    model_result = await mock_anki.create_note_type(
        "Integration Model",
        ["Term", "Definition", "Example"],
        [
            {
                "name": "Card 1",
                "qfmt": "{{Term}}",
                "afmt": "{{FrontSide}}<hr>{{Definition}}<br>{{Example}}",
            }
        ],
    )
    assert model_result["created"] is True

    # Create a note
    note_result = await mock_anki.create_note(
        model_name="Integration Model",
        fields={
            "Term": "Mutation",
            "Definition": "A change in data",
            "Example": "Creating, updating, or deleting records",
        },
        deck_name="Integration Test",
        tags=["programming", "database"],
    )
    note_id = note_result["id"]

    # Update the note
    update_result = await mock_anki.update_note(
        note_id,
        fields={"Example": "CRUD operations: Create, Read, Update, Delete"},
        tags=["programming", "database", "crud"],
    )
    assert "crud" in update_result["tags"]

    # Verify the updated note
    updated_note = await mock_anki.get_note(note_id)
    assert (
        updated_note["fields"]["Example"]
        == "CRUD operations: Create, Read, Update, Delete"
    )

    # Delete the note
    delete_result = await mock_anki.delete_note(note_id)
    assert delete_result["deleted"] is True
