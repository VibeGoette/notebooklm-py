"""NotebookLM → Anki / anki-maxed card export.

Pure conversion helpers. The CLI (``notebooklm anki export``) is the adapter;
nothing here talks to Google, AnkiConnect, or anki-llm.
"""

from .convert import (
    AnkiCard,
    AnkiExport,
    build_export,
    cards_from_flashcards,
    cards_from_guide,
    cards_from_notes,
    cards_from_qa_pairs,
    cards_from_quiz,
    cards_from_transcript,
)
from .parse import parse_export_payload
from .serialize import serialize_export

__all__ = [
    "AnkiCard",
    "AnkiExport",
    "build_export",
    "cards_from_flashcards",
    "cards_from_guide",
    "cards_from_notes",
    "cards_from_qa_pairs",
    "cards_from_quiz",
    "cards_from_transcript",
    "parse_export_payload",
    "serialize_export",
]
