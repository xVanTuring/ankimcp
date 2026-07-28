"""Interface for accessing Anki data."""

import html
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .permissions import PermissionAction, PermissionError, PermissionManager

try:
    # When running as an Anki addon
    from anki.cards import Card
    from anki.collection import Collection
    from anki.notes import Note, NoteId
    from aqt import mw

    ANKI_AVAILABLE = True
except ImportError:
    # When running standalone or testing
    ANKI_AVAILABLE = False
    mw = None
    if not TYPE_CHECKING:
        Collection = Any
        Note = Any
        Card = Any
        NoteId = int  # type: ignore


# Mirrors Anki's own strip_html: tags are removed without inserting whitespace,
# so markup used inside a word (e.g. "con<b></b>sole") collapses back to "console".
_RE_COMMENT = re.compile(r"(?s)<!--.*?-->")
_RE_STYLE = re.compile(r"(?si)<style.*?>.*?</style>")
_RE_SCRIPT = re.compile(r"(?si)<script.*?>.*?</script>")
_RE_TAG = re.compile(r"(?s)<.*?>")
_RE_WHITESPACE = re.compile(r"\s+")

# Anki's mature threshold: cards with an interval of 21+ days are "mature".
MATURE_IVL_DAYS = 21

QUEUE_SUSPENDED = -1
QUEUE_BURIED = (-2, -3)


def _plain_text(value: str) -> str:
    """Strip HTML markup from a field value and collapse whitespace."""
    text = _RE_COMMENT.sub("", value)
    text = _RE_STYLE.sub("", text)
    text = _RE_SCRIPT.sub("", text)
    text = _RE_TAG.sub("", text)
    text = html.unescape(text)
    return _RE_WHITESPACE.sub(" ", text).strip()


def _card_state(card_type: int, ivl: int) -> str:
    """Map Anki's card type + interval onto a single learning-state label."""
    if card_type == 0:
        return "new"
    if card_type == 1:
        return "learning"
    if card_type == 3:
        return "relearning"
    return "mature" if ivl >= MATURE_IVL_DAYS else "young"


def _group_by_note(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge card entries that share a nid into one entry per note.

    Requested note fields move onto the note entry; per-card scheduling
    state stays in the nested "cards" list (without the duplicated nid).
    """
    notes: Dict[int, Dict[str, Any]] = {}
    for card in cards:
        note = notes.setdefault(card["nid"], {"nid": card["nid"], "cards": []})
        if "fields" in card and "fields" not in note:
            note["fields"] = card["fields"]
        note["cards"].append(
            {key: value for key, value in card.items() if key not in ("nid", "fields")}
        )
    return list(notes.values())


class AnkiInterface:
    """Interface for accessing Anki collection data."""

    def __init__(
        self,
        collection: Optional["Collection"] = None,
        permission_config: Optional[Dict] = None,
    ):
        """Initialize with a collection (uses mw.col if not provided)."""
        if collection:
            self.col = collection
        elif ANKI_AVAILABLE and mw and mw.col:
            self.col = mw.col
        else:
            raise RuntimeError("No Anki collection available")

        # Initialize permission manager
        if permission_config:
            self.permissions = PermissionManager(permission_config)
        else:
            # Default permissive configuration
            self.permissions = PermissionManager(
                {
                    "permissions": {
                        "global": {"read": True, "write": True, "delete": True},
                        "mode": "denylist",
                        "deck_permissions": {"allowlist": [], "denylist": []},
                        "protected_decks": [],
                    }
                }
            )

    async def list_decks(self) -> List[Dict[str, Any]]:
        """List all available decks."""
        decks = []
        for deck_id in self.col.decks.all_names_and_ids():
            deck = self.col.decks.get(deck_id.id)  # type: ignore
            if deck:
                decks.append(
                    {
                        "id": deck_id.id,
                        "name": deck_id.name,
                        "card_count": len(self.col.decks.cids(deck_id.id)),  # type: ignore
                        "is_filtered": deck.get("dyn", 0) != 0,
                    }
                )
        # Filter based on read permissions
        return self.permissions.filter_decks(decks)

    async def get_deck_info(self, deck_name: str) -> Dict[str, Any]:
        """Get detailed information about a specific deck."""
        # Check read permission
        self.permissions.check_deck_permission(deck_name, PermissionAction.READ)

        deck_id = self.col.decks.id_for_name(deck_name)
        if not deck_id:
            raise ValueError(f"Deck not found: {deck_name}")

        deck = self.col.decks.get(deck_id)
        if not deck:
            raise ValueError(f"Deck not found: {deck_name}")

        card_ids = self.col.decks.cids(deck_id)  # type: ignore

        # Get review statistics
        new_count = len([cid for cid in card_ids if self.col.get_card(cid).type == 0])
        learning_count = len(
            [cid for cid in card_ids if self.col.get_card(cid).type == 1]
        )
        review_count = len(
            [cid for cid in card_ids if self.col.get_card(cid).type == 2]
        )

        return {
            "id": deck_id,
            "name": deck_name,
            "card_count": len(card_ids),
            "new_count": new_count,
            "learning_count": learning_count,
            "review_count": review_count,
            "is_filtered": deck.get("dyn", 0) != 0,
            "config": deck,
        }

    async def search_notes(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search for notes using Anki's search syntax."""
        note_ids = self.col.find_notes(query)[:limit]
        notes = []

        for nid in note_ids:
            note = self.col.get_note(nid)  # type: ignore
            notes.append(await self._note_to_dict(note))

        return notes

    async def search_card_states(
        self,
        query: str,
        fields: Optional[List[str]] = None,
        limit: int = 0,
        strip_html: bool = True,
        by_note: bool = False,
    ) -> Dict[str, Any]:
        """Search cards, returning scheduling state plus only the requested fields.

        Unlike search_notes, this never returns whole notes, so it stays small
        enough to bulk-export a deck of thousands of cards. With by_note=True,
        cards that share a note are merged into one entry per note.
        """
        card_ids = self.col.find_cards(query)
        matched = len(card_ids)
        if limit > 0:
            card_ids = card_ids[:limit]

        wanted = list(fields or [])
        deck_names: Dict[int, str] = {}
        deck_readable: Dict[int, bool] = {}
        note_fields: Dict[int, Dict[str, str]] = {}
        cards: List[Dict[str, Any]] = []

        for cid in card_ids:
            card = self.col.get_card(cid)

            if card.did not in deck_names:
                deck_names[card.did] = self.col.decks.name(card.did)
                deck_readable[card.did] = self._is_readable(deck_names[card.did])
            if not deck_readable[card.did]:
                continue

            entry: Dict[str, Any] = {
                "cid": card.id,
                "nid": card.nid,
                "deck": deck_names[card.did],
                "state": _card_state(card.type, card.ivl),
                "ivl": card.ivl,
                "due": card.due,
                "reps": card.reps,
                "lapses": card.lapses,
                "factor": card.factor,
            }
            if card.queue == QUEUE_SUSPENDED:
                entry["suspended"] = True
            if card.queue in QUEUE_BURIED:
                entry["buried"] = True

            if wanted:
                if card.nid not in note_fields:
                    note = self.col.get_note(card.nid)  # type: ignore
                    note_fields[card.nid] = self._select_fields(
                        note, wanted, strip_html
                    )
                entry["fields"] = note_fields[card.nid]

            cards.append(entry)

        if by_note:
            notes = _group_by_note(cards)
            return {
                "count": len(notes),
                "matched": matched,
                "truncated": limit > 0 and matched > limit,
                "notes": notes,
            }

        return {
            "count": len(cards),
            "matched": matched,
            "truncated": limit > 0 and matched > limit,
            "cards": cards,
        }

    def _is_readable(self, deck_name: str) -> bool:
        """Whether the deck may be read, without raising."""
        try:
            self.permissions.check_deck_permission(deck_name, PermissionAction.READ)
            return True
        except PermissionError:
            return False

    @staticmethod
    def _select_fields(
        note: "Note", wanted: List[str], strip_html: bool
    ) -> Dict[str, str]:
        """Pull just the named fields off a note. Unknown names are skipped."""
        model = note.note_type()
        if not model:
            return {}

        names = [f["name"] for f in model["flds"]]
        selected = {}
        for name in wanted:
            if name in names:
                value = note.fields[names.index(name)]
                selected[name] = _plain_text(value) if strip_html else value
        return selected

    async def get_note(self, note_id: int) -> Dict[str, Any]:
        """Get detailed information about a specific note."""
        note = self.col.get_note(note_id)  # type: ignore
        return await self._note_to_dict(note)

    async def get_cards_for_note(self, note_id: int) -> List[Dict[str, Any]]:
        """Get all cards associated with a note."""
        note = self.col.get_note(note_id)  # type: ignore
        cards = []

        for card_id in note.card_ids():
            card = self.col.get_card(card_id)
            cards.append(await self._card_to_dict(card))

        return cards

    async def get_review_stats(self, deck_name: Optional[str] = None) -> Dict[str, Any]:
        """Get review statistics for a deck or overall."""
        if deck_name:
            deck_id = self.col.decks.id_for_name(deck_name)
            if not deck_id:
                raise ValueError(f"Deck not found: {deck_name}")
            card_ids = self.col.decks.cids(deck_id)  # type: ignore
        else:
            card_ids = self.col.find_cards("")

        total_cards = len(card_ids)
        new_cards = 0
        learning_cards = 0
        review_cards = 0

        for cid in card_ids:
            card = self.col.get_card(cid)
            if card.type == 0:
                new_cards += 1
            elif card.type == 1:
                learning_cards += 1
            elif card.type == 2:
                review_cards += 1

        return {
            "deck_name": deck_name or "All Decks",
            "total_cards": total_cards,
            "new_cards": new_cards,
            "learning_cards": learning_cards,
            "review_cards": review_cards,
            "mature_cards": len(
                [cid for cid in card_ids if self.col.get_card(cid).ivl >= 21]
            ),
        }

    async def _note_to_dict(self, note: "Note") -> Dict[str, Any]:
        """Convert a note to a dictionary."""
        model = note.note_type()
        fields = {}

        if model:
            for i, field in enumerate(model["flds"]):
                fields[field["name"]] = note.fields[i]

        return {
            "id": note.id,
            "model_name": model["name"] if model else "Unknown",
            "fields": fields,
            "tags": note.tags,
            "card_count": len(note.card_ids()),
        }

    async def _card_to_dict(self, card: "Card") -> Dict[str, Any]:
        """Convert a card to a dictionary."""
        card.note()  # Ensure note is loaded
        deck_name = self.col.decks.name(card.did)

        return {
            "id": card.id,
            "note_id": card.nid,
            "deck_name": deck_name,
            "type": card.type,  # 0=new, 1=learning, 2=review
            "queue": card.queue,
            "due": card.due,
            "interval": card.ivl,
            "ease_factor": card.factor,
            "reviews": card.reps,
            "lapses": card.lapses,
            "last_review": getattr(card, "last_review", 0),
        }

    async def list_note_types(self) -> List[Dict[str, Any]]:
        """List all available note types (models)."""
        note_types = []
        for model in self.col.models.all():
            fields = [f["name"] for f in model.get("flds", [])]
            templates = [t["name"] for t in model.get("tmpls", [])]
            note_types.append(
                {
                    "id": model["id"],
                    "name": model["name"],
                    "fields": fields,
                    "templates": templates,
                    "field_count": len(fields),
                    "template_count": len(templates),
                }
            )
        return note_types

    async def create_deck(self, deck_name: str) -> Dict[str, Any]:
        """Create a new deck."""
        # Check create permission
        self.permissions.check_deck_permission(deck_name, PermissionAction.CREATE)

        deck_id = self.col.decks.id(deck_name)  # This creates if doesn't exist
        deck = self.col.decks.get(deck_id) if deck_id else None

        return {
            "id": deck_id,
            "name": deck_name,
            "created": True,
            "config": deck,
        }

    async def create_note_type(
        self, name: str, fields: List[str], templates: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """Create a new note type (model)."""
        models = self.col.models

        # Create a new model
        model = models.new(name)

        # Add fields
        for field_name in fields:
            field = models.new_field(field_name)
            models.add_field(model, field)

        # Add templates (card types)
        for template in templates:
            t = models.new_template(template.get("name", "Card 1"))
            t["qfmt"] = template.get("qfmt", "{{" + fields[0] + "}}")
            t["afmt"] = template.get(
                "afmt", "{{FrontSide}}\n\n<hr id=answer>\n\n{{" + fields[-1] + "}}"
            )
            models.add_template(model, t)

        # Save the model
        models.save(model)

        return {
            "id": model["id"],
            "name": model["name"],
            "field_count": len(model["flds"]),
            "template_count": len(model["tmpls"]),
            "created": True,
        }

    async def create_note(
        self,
        model_name: str,
        fields: Dict[str, str],
        deck_name: str,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new note."""
        # Check deck write permission
        self.permissions.check_deck_permission(deck_name, PermissionAction.WRITE)

        # Check tag permissions if tags provided
        if tags:
            self.permissions.check_tag_permission(tags, PermissionAction.WRITE)

        # Check note type permission (READ = allowed to use this note type)
        self.permissions.check_note_type_permission(model_name, PermissionAction.READ)

        # Get model
        model_id = self.col.models.id_for_name(model_name)
        if not model_id:
            raise ValueError(f"Model not found: {model_name}")

        model = self.col.models.get(model_id)
        if not model:
            raise ValueError(f"Model not found: {model_name}")

        # Get deck
        deck_id = self.col.decks.id_for_name(deck_name)
        if not deck_id:
            raise ValueError(f"Deck not found: {deck_name}")

        # Create note
        note = self.col.new_note(model)

        # Set fields
        for field in model["flds"]:
            field_name = field["name"]
            if field_name in fields:
                note[field_name] = fields[field_name]

        # Set tags
        if tags:
            note.tags = tags

        # Add to collection
        self.col.add_note(note, deck_id)

        return await self._note_to_dict(note)

    async def update_note(
        self,
        note_id: int,
        fields: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        return_fields: Optional[List[str]] = None,
        strip_html: bool = False,
    ) -> Dict[str, Any]:
        """Update an existing note.

        Returns a minimal confirmation by default; pass return_fields to read
        back specific fields after the update (["*"] returns all fields).
        Returned field values are raw (with HTML) unless strip_html is set.

        Raises ValueError if any key in `fields` is not a field of the note's
        note type — field names must match exactly (case and whitespace
        sensitive), otherwise the update would be silently dropped.
        """
        note = self.col.get_note(NoteId(note_id))

        # Check current note's tags for permissions
        self.permissions.check_tag_permission(note.tags, PermissionAction.WRITE)

        # Check new tags if provided
        if tags is not None:
            self.permissions.check_tag_permission(tags, PermissionAction.WRITE)

        # Update fields
        updated_fields: List[str] = []
        if fields:
            model = note.note_type()
            if model:
                valid_names = [field["name"] for field in model["flds"]]
                unknown = [name for name in fields if name not in valid_names]
                if unknown:
                    raise ValueError(
                        f"Unknown field(s) for note type "
                        f"'{model['name']}': {unknown}. "
                        f"Valid fields: {valid_names}"
                    )
                for field_name in valid_names:
                    if field_name in fields:
                        note[field_name] = fields[field_name]
                        updated_fields.append(field_name)

        # Update tags
        if tags is not None:
            note.tags = tags

        # Save changes
        self.col.update_note(note)

        result: Dict[str, Any] = {
            "note_id": note_id,
            "updated_fields": updated_fields,
            "tags_updated": tags is not None,
            "success": True,
        }
        if tags is not None:
            result["tags"] = note.tags
        if return_fields:
            model = note.note_type()
            names = [f["name"] for f in model["flds"]] if model else []
            wanted = (
                names
                if "*" in return_fields
                else [n for n in return_fields if n in names]
            )
            result["fields"] = {
                n: (
                    _plain_text(note.fields[names.index(n)])
                    if strip_html
                    else note.fields[names.index(n)]
                )
                for n in wanted
            }
        return result

    async def update_notes(
        self,
        updates: List[Dict[str, Any]],
        return_fields: Optional[List[str]] = None,
        strip_html: bool = False,
    ) -> Dict[str, Any]:
        """Update multiple notes in one call.

        Each item is {"note_id": int, "fields"?: {...}, "tags"?: [...]}.
        A failure on one note does not abort the rest; per-note errors are
        reported in the results list.
        """
        results: List[Dict[str, Any]] = []
        succeeded = 0

        for item in updates:
            note_id = item.get("note_id")
            try:
                if note_id is None:
                    raise ValueError("Missing note_id")
                result = await self.update_note(
                    note_id,
                    fields=item.get("fields"),
                    tags=item.get("tags"),
                    return_fields=return_fields,
                    strip_html=strip_html,
                )
                succeeded += 1
                results.append(result)
            except Exception as e:
                results.append({"note_id": note_id, "success": False, "error": str(e)})

        return {
            "count": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": results,
        }

    async def delete_note(self, note_id: int) -> Dict[str, Any]:
        """Delete a note and all its cards."""
        note = self.col.get_note(NoteId(note_id))

        # Check permission to delete based on tags
        self.permissions.check_tag_permission(note.tags, PermissionAction.DELETE)

        # Check deck permissions for all cards
        for card_id in note.card_ids():
            card = self.col.get_card(card_id)
            deck_name = self.col.decks.name(card.did)
            self.permissions.check_deck_permission(deck_name, PermissionAction.DELETE)

        card_count = len(note.card_ids())

        # Remove the note (this also removes all associated cards)
        self.col.remove_notes([NoteId(note_id)])

        return {
            "note_id": note_id,
            "deleted": True,
            "cards_deleted": card_count,
        }

    async def delete_deck(self, deck_name: str) -> Dict[str, Any]:
        """Delete a deck and all its cards."""
        # Check permission to delete this deck
        self.permissions.check_deck_permission(deck_name, PermissionAction.DELETE)

        # Get deck ID
        deck_id = self.col.decks.id_for_name(deck_name)
        if not deck_id:
            raise ValueError(f"Deck not found: {deck_name}")

        # Get card count before deletion
        card_count = self.col.decks.card_count(deck_id, include_subdecks=False)

        # Remove the deck (this also removes all cards in the deck)
        self.col.decks.remove([deck_id])

        return {
            "deck_name": deck_name,
            "deleted": True,
            "cards_deleted": card_count,
        }

    async def update_deck(
        self,
        deck_name: str,
        new_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a deck's properties (name and/or description)."""
        # Check permission to write to this deck
        self.permissions.check_deck_permission(deck_name, PermissionAction.WRITE)

        # If renaming, also check permission for the new name
        if new_name is not None:
            self.permissions.check_deck_permission(new_name, PermissionAction.WRITE)

        # Get deck ID
        deck_id = self.col.decks.id_for_name(deck_name)
        if not deck_id:
            raise ValueError(f"Deck not found: {deck_name}")

        # Get the deck
        deck = self.col.decks.get(deck_id)
        if not deck:
            raise ValueError(f"Deck not found: {deck_name}")

        updated_fields = []
        result_name = deck_name

        # Update name if provided
        if new_name is not None:
            deck["name"] = new_name
            updated_fields.append("name")
            result_name = new_name

        # Update description if provided
        if description is not None:
            deck["desc"] = description
            updated_fields.append("description")

        # Save changes
        self.col.decks.save(deck)

        return {
            "deck_name": result_name,
            "deck_id": deck_id,
            "updated": True,
            "updated_fields": updated_fields,
        }

    async def sync(self) -> Dict[str, Any]:
        """Sync Anki collection with AnkiWeb."""
        if mw is not None:
            mw.taskman.run_on_main(lambda mw=mw: mw.on_sync_button_clicked())
        return {
            "synced": True,
            "message": "Collection sync unsupported",
        }
