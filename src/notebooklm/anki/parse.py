"""Parse local NotebookLM export files into converter inputs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .convert import (
    AnkiCard,
    cards_from_flashcards,
    cards_from_guide,
    cards_from_notes,
    cards_from_qa_pairs,
    cards_from_quiz,
    cards_from_transcript,
)

InputKind = Literal["auto", "history", "notes", "guide", "flashcards", "quiz", "file"]


@dataclass(frozen=True)
class ParsedPayload:
    """Normalized local-file payload plus the detected source kind."""

    cards: tuple[AnkiCard, ...]
    notebook_id: str | None
    detected_kind: str


def parse_export_payload(
    text: str,
    *,
    kind: InputKind = "auto",
    extra_tags: tuple[str, ...] = (),
    cloze: bool = False,
    notebook_id: str | None = None,
    source_title: str | None = None,
) -> ParsedPayload:
    """Parse JSON / JSONL / Markdown into Anki cards.

    Accepts ``notebooklm history --json``, ``download flashcards --format json``,
    ``download quiz --format json``, source-guide markdown, Q&A notes, JSONL,
    and the notebook-transcript fixture schema.
    """
    stripped = text.strip()
    if not stripped:
        return ParsedPayload(cards=(), notebook_id=notebook_id, detected_kind=kind)

    data = _load_structured(stripped)
    if data is None:
        cards = cards_from_notes(
            [{"id": "markdown", "title": source_title or "Notes", "content": stripped}],
            notebook_id=notebook_id,
            extra_tags=extra_tags,
            source_titles=(source_title,) if source_title else (),
            cloze=cloze,
        )
        if not cards:
            cards = cards_from_guide(
                stripped,
                notebook_id=notebook_id,
                source_title=source_title,
                extra_tags=extra_tags,
                cloze=cloze,
            )
        return ParsedPayload(cards=cards, notebook_id=notebook_id, detected_kind="notes")

    resolved_id = notebook_id or _notebook_id_from(data)
    detected = _detect_kind(data, kind)
    cards = _cards_for_kind(
        data,
        detected,
        extra_tags=extra_tags,
        cloze=cloze,
        notebook_id=resolved_id,
        source_title=source_title,
    )
    return ParsedPayload(cards=cards, notebook_id=resolved_id, detected_kind=detected)


def _load_structured(text: str) -> Mapping[str, Any] | None:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = _load_jsonl(text)
        if loaded is None:
            return None
    if isinstance(loaded, list):
        return {"items": loaded}
    if isinstance(loaded, Mapping):
        return loaded
    return None


def _load_jsonl(text: str) -> list[Any] | None:
    rows: list[Any] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rows.append(json.loads(stripped))
        except json.JSONDecodeError:
            return None
    return rows or None


def _notebook_id_from(data: Mapping[str, Any]) -> str | None:
    value = data.get("notebook_id") or data.get("id")
    if value:
        return str(value)
    return None


def _detect_kind(data: Mapping[str, Any], kind: InputKind) -> str:
    if kind not in {"auto", "file"}:
        return kind
    if data.get("qa_pairs") or data.get("history"):
        if data.get("notes") or data.get("guides") or data.get("cards") or data.get("questions"):
            return "file"
        return "history"
    if data.get("questions") or data.get("quiz"):
        return "quiz"
    if data.get("cards") or data.get("flashcards"):
        return "flashcards"
    if data.get("notes") and not data.get("summary"):
        return "notes"
    if data.get("summary") or data.get("keywords"):
        return "guide"
    if data.get("items"):
        return "file"
    return "file"


def _cards_for_kind(
    data: Mapping[str, Any],
    kind: str,
    *,
    extra_tags: tuple[str, ...],
    cloze: bool,
    notebook_id: str | None,
    source_title: str | None,
) -> tuple[AnkiCard, ...]:
    titles = (source_title,) if source_title else ()
    if kind == "history":
        pairs = data.get("qa_pairs") or data.get("history") or data.get("items") or ()
        return cards_from_qa_pairs(
            list(pairs),
            notebook_id=notebook_id,
            extra_tags=extra_tags,
            source_titles=titles,
            cloze=cloze,
        )
    if kind == "notes":
        notes = data.get("notes") or data.get("items") or ()
        return cards_from_notes(
            list(notes),
            notebook_id=notebook_id,
            extra_tags=extra_tags,
            source_titles=titles,
            cloze=cloze,
        )
    if kind == "guide":
        return cards_from_guide(
            str(data.get("summary") or data.get("content") or ""),
            keywords=tuple(data.get("keywords") or ()),
            notebook_id=notebook_id,
            source_id=_as_str(data.get("source_id")),
            source_title=source_title or _as_str(data.get("source_title") or data.get("title")),
            extra_tags=extra_tags,
            cloze=cloze,
        )
    if kind == "flashcards":
        return cards_from_flashcards(
            data,
            notebook_id=notebook_id,
            extra_tags=extra_tags,
            source_titles=titles,
            cloze=cloze,
        )
    if kind == "quiz":
        return cards_from_quiz(
            data,
            notebook_id=notebook_id,
            extra_tags=extra_tags,
            source_titles=titles,
            cloze=cloze,
        )
    if data.get("items") and not _transcript_keys(data):
        items = data["items"]
        if (
            items
            and isinstance(items[0], Mapping)
            and ("question" in items[0] or "front" in items[0])
        ):
            return cards_from_qa_pairs(
                list(items),
                notebook_id=notebook_id,
                extra_tags=extra_tags,
                source_titles=titles,
                cloze=cloze,
            )
    return cards_from_transcript(data, extra_tags=extra_tags, cloze=cloze)


def _transcript_keys(data: Mapping[str, Any]) -> bool:
    return any(key in data for key in ("qa_pairs", "notes", "guides", "history"))


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
