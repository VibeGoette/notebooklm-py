"""Serialize an :class:`AnkiExport` to YAML / TSV / JSON / JSONL."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Literal

from .convert import AnkiCard, AnkiExport

ExportFormat = Literal["yaml", "tsv", "json", "jsonl"]
SCHEMA_ID = "notebooklm.anki.v1"
LOKALLERN_SCHEMA_ID = "lokallern.card.v1"


def serialize_export(export: AnkiExport, fmt: ExportFormat) -> str:
    """Render ``export`` in the requested Anki / anki-maxed format."""
    if fmt == "yaml":
        return _to_yaml(export)
    if fmt == "tsv":
        return _to_tsv(export)
    if fmt == "jsonl":
        return _to_jsonl(export)
    if fmt == "json":
        return json.dumps(_to_json_object(export), indent=2, ensure_ascii=False) + "\n"
    raise ValueError(f"Unsupported Anki export format: {fmt}")


def card_as_dict(card: AnkiCard, *, deck: str | None, notebook_id: str | None) -> dict[str, object]:
    """Stable JSON object used by ``json``, ``jsonl``, and ``--json`` envelopes."""
    return {
        "schema": LOKALLERN_SCHEMA_ID,
        "front": card.front,
        "back": card.back,
        "text": card.text,
        "note_type": card.note_type,
        "template": card.template,
        "source_ref": card.source_ref,
        "status": "draft",
        "tags": list(card.tags),
        "deck": deck,
        "notebook_id": notebook_id,
    }


def _to_json_object(export: AnkiExport) -> dict[str, object]:
    return {
        "schema": SCHEMA_ID,
        "deck": export.deck,
        "notebook_id": export.notebook_id,
        "source_kind": export.source_kind,
        "count": export.count,
        "cards": [
            card_as_dict(card, deck=export.deck, notebook_id=export.notebook_id)
            for card in export.cards
        ],
    }


def _to_jsonl(export: AnkiExport) -> str:
    lines = [
        json.dumps(
            card_as_dict(card, deck=export.deck, notebook_id=export.notebook_id),
            ensure_ascii=False,
        )
        for card in export.cards
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _to_tsv(export: AnkiExport) -> str:
    buffer = StringIO()
    buffer.write("#separator:tab\n")
    buffer.write("#html:false\n")
    buffer.write("#notetype column:1\n")
    buffer.write("#tags column:5\n")
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["Note type", "Front", "Back", "Text", "Tags"])
    for card in export.cards:
        note_type = "Cloze" if card.note_type == "cloze" else "Basic"
        writer.writerow([note_type, card.front, card.back, card.text, " ".join(card.tags)])
    return buffer.getvalue()


def _to_yaml(export: AnkiExport) -> str:
    """Emit anki-llm's import shape: a YAML list of field objects.

    Field names match the stock Anki Basic (``Front`` / ``Back``) and Cloze
    (``Text``) models so ``anki-llm import --note-type Basic`` (or ``Cloze``)
    can consume the file. Unused fields are ignored by anki-llm.
    """
    chunks: list[str] = []
    for card in export.cards:
        chunks.append("- Front: " + _yaml_scalar(card.front))
        chunks.append("  Back: " + _yaml_scalar(card.back))
        chunks.append("  Text: " + _yaml_scalar(card.text))
        chunks.append("  Tags: " + _yaml_scalar(" ".join(card.tags)))
    return "\n".join(chunks) + ("\n" if chunks else "")


def _yaml_scalar(value: str) -> str:
    """Quote every scalar with JSON so the YAML stays valid without PyYAML."""
    return json.dumps(value, ensure_ascii=False)
