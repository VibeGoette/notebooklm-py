"""Pure NotebookLM study-material → Anki card converters."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

NoteType = Literal["basic", "cloze"]

_CLOZE_RE = re.compile(r"\{\{c\d+::.+?\}\}", re.DOTALL)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_QA_BLOCK_RE = re.compile(
    r"\*\*Q:\*\*\s*(.*?)\s*\*\*A:\*\*\s*(.*?)(?=\n\s*\*\*Q:\*\*|\n\s*###\s+Turn|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
_TAG_SAFE_RE = re.compile(r"[^A-Za-z0-9:_-]+")


@dataclass(frozen=True)
class AnkiCard:
    """One Anki-compatible note with Basic and/or Cloze fields.

    ``front`` / ``back`` map to the stock Basic note type. ``text`` maps to the
    stock Cloze ``Text`` field. Serializers emit all four so anki-llm can
    import the same file as Basic or Cloze (unused fields are ignored).
    """

    front: str
    back: str
    tags: tuple[str, ...] = ()
    note_type: NoteType = "basic"
    text: str = ""
    source_ref: str = ""
    template: str = "definition"

    def __post_init__(self) -> None:
        if self.note_type == "cloze" and not self.text:
            object.__setattr__(self, "text", self.front)


@dataclass(frozen=True)
class AnkiExport:
    """A converted deck ready for serialization."""

    cards: tuple[AnkiCard, ...]
    notebook_id: str | None = None
    deck: str | None = None
    source_kind: str = "file"

    @property
    def count(self) -> int:
        """Number of cards in the export."""
        return len(self.cards)


def sanitize_tag(value: str) -> str:
    """Turn a free-text label into an Anki-safe tag (no spaces)."""
    cleaned = _TAG_SAFE_RE.sub("_", value.strip())
    return cleaned.strip("_")


def default_tags(
    *,
    notebook_id: str | None = None,
    extra: Sequence[str] = (),
    source_titles: Sequence[str] = (),
) -> tuple[str, ...]:
    """Build the v1 tag set: ``notebooklm``, notebook id, source titles, extras."""
    tags: list[str] = ["notebooklm"]
    if notebook_id:
        tags.append(f"nb:{sanitize_tag(notebook_id)}")
    for title in source_titles:
        slug = sanitize_tag(title)
        if slug:
            tags.append(slug)
    for tag in extra:
        slug = sanitize_tag(tag)
        if slug:
            tags.append(slug)
    # Preserve order while dropping duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            unique.append(tag)
    return tuple(unique)


def _looks_like_cloze(text: str) -> bool:
    return bool(_CLOZE_RE.search(text))


def _bold_to_cloze(text: str) -> str:
    """Turn ``**term**`` spans into sequential Anki cloze deletions."""
    index = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal index
        index += 1
        return f"{{{{c{index}::{match.group(1)}}}}}"

    return _BOLD_RE.sub(_replace, text)


def _card(
    front: str,
    back: str,
    *,
    tags: Sequence[str],
    source_ref: str,
    template: str = "definition",
    cloze: bool = False,
) -> AnkiCard | None:
    front = front.strip()
    back = back.strip()
    if not front or not back:
        return None
    text = ""
    note_type: NoteType = "basic"
    if cloze:
        clozed = _bold_to_cloze(front) if _BOLD_RE.search(front) else front
        if _looks_like_cloze(clozed) or _looks_like_cloze(back):
            note_type = "cloze"
            text = clozed if _looks_like_cloze(clozed) else _bold_to_cloze(back)
            template = "cloze"
    elif _looks_like_cloze(front) or _looks_like_cloze(back):
        note_type = "cloze"
        text = front if _looks_like_cloze(front) else back
        template = "cloze"
    return AnkiCard(
        front=front,
        back=back,
        tags=tuple(tags),
        note_type=note_type,
        text=text,
        source_ref=source_ref,
        template=template,
    )


def cards_from_qa_pairs(
    pairs: Sequence[tuple[str, str] | Mapping[str, Any]],
    *,
    notebook_id: str | None = None,
    extra_tags: Sequence[str] = (),
    source_titles: Sequence[str] = (),
    source_ref_prefix: str = "history",
    cloze: bool = False,
) -> tuple[AnkiCard, ...]:
    """Map Q&A turns (history, ask, or transcript) to Basic/Cloze cards."""
    tags = default_tags(
        notebook_id=notebook_id, extra=extra_tags, source_titles=source_titles
    )
    cards: list[AnkiCard] = []
    for index, raw in enumerate(pairs, 1):
        if isinstance(raw, Mapping):
            question = str(raw.get("question") or raw.get("q") or raw.get("front") or "")
            answer = str(raw.get("answer") or raw.get("a") or raw.get("back") or "")
        else:
            question, answer = raw
        card = _card(
            question,
            answer,
            tags=tags,
            source_ref=f"notebooklm:{notebook_id or 'unknown'}#{source_ref_prefix}:{index}",
            cloze=cloze,
        )
        if card is not None:
            cards.append(card)
    return tuple(cards)


def cards_from_notes(
    notes: Sequence[Any],
    *,
    notebook_id: str | None = None,
    extra_tags: Sequence[str] = (),
    source_titles: Sequence[str] = (),
    cloze: bool = False,
) -> tuple[AnkiCard, ...]:
    """Map notebook notes to cards.

    Notes that already use the ``**Q:**`` / ``**A:**`` history format explode
    into one card per pair. Other notes become a single Basic card
    (title → Front, body → Back).
    """
    tags = default_tags(
        notebook_id=notebook_id, extra=extra_tags, source_titles=source_titles
    )
    cards: list[AnkiCard] = []
    for note in notes:
        note_id = str(getattr(note, "id", "") or _mapping_get(note, "id") or "note")
        title = str(getattr(note, "title", "") or _mapping_get(note, "title") or "")
        content = str(getattr(note, "content", "") or _mapping_get(note, "content") or "")
        pairs = _qa_pairs_from_text(content)
        if pairs:
            cards.extend(
                cards_from_qa_pairs(
                    pairs,
                    notebook_id=notebook_id,
                    extra_tags=extra_tags,
                    source_titles=source_titles,
                    source_ref_prefix=f"note:{note_id}",
                    cloze=cloze,
                )
            )
            continue
        card = _card(
            title or "Untitled note",
            content,
            tags=tags,
            source_ref=f"notebooklm:{notebook_id or 'unknown'}#note:{note_id}",
            cloze=cloze,
        )
        if card is not None:
            cards.append(card)
    return tuple(cards)


def cards_from_guide(
    summary: str,
    *,
    keywords: Sequence[str] = (),
    notebook_id: str | None = None,
    source_id: str | None = None,
    source_title: str | None = None,
    extra_tags: Sequence[str] = (),
    cloze: bool = False,
) -> tuple[AnkiCard, ...]:
    """Map a source-guide summary (and optional keywords) to cards.

    Markdown ``##`` headings become Front, the following paragraph Back.
    Remaining ``**bold**`` keywords and the keyword list become definition cards.
    """
    titles = (source_title,) if source_title else ()
    tags = default_tags(notebook_id=notebook_id, extra=extra_tags, source_titles=titles)
    ref = f"notebooklm:{notebook_id or 'unknown'}#guide:{source_id or 'source'}"
    cards: list[AnkiCard] = []

    for heading, body in _heading_sections(summary):
        card = _card(heading, body, tags=tags, source_ref=ref, cloze=cloze)
        if card is not None:
            cards.append(card)

    if not cards and summary.strip():
        overview = _card(
            source_title or "What does this source cover?",
            summary.strip(),
            tags=tags,
            source_ref=ref,
            cloze=cloze,
        )
        if overview is not None:
            cards.append(overview)

    for keyword in keywords:
        word = keyword.strip()
        if not word:
            continue
        card = _card(
            f"What is {word}?",
            _keyword_context(summary, word) or word,
            tags=tags,
            source_ref=ref,
            template="definition",
            cloze=cloze,
        )
        if card is not None:
            cards.append(card)
    return tuple(cards)


def cards_from_flashcards(
    payload: Mapping[str, Any] | Sequence[Any],
    *,
    notebook_id: str | None = None,
    extra_tags: Sequence[str] = (),
    source_titles: Sequence[str] = (),
    cloze: bool = False,
) -> tuple[AnkiCard, ...]:
    """Map NotebookLM flashcard JSON (``download flashcards --format json``)."""
    raw_cards = _flashcard_rows(payload)
    pairs = []
    for row in raw_cards:
        if not isinstance(row, Mapping):
            continue
        front = str(row.get("front") or row.get("f") or row.get("question") or "")
        back = str(row.get("back") or row.get("b") or row.get("answer") or "")
        pairs.append((front, back))
    return cards_from_qa_pairs(
        pairs,
        notebook_id=notebook_id,
        extra_tags=extra_tags,
        source_titles=source_titles,
        source_ref_prefix="flashcards",
        cloze=cloze,
    )


def cards_from_quiz(
    payload: Mapping[str, Any] | Sequence[Any],
    *,
    notebook_id: str | None = None,
    extra_tags: Sequence[str] = (),
    source_titles: Sequence[str] = (),
    cloze: bool = False,
) -> tuple[AnkiCard, ...]:
    """Map NotebookLM quiz JSON (``download quiz --format json``) to Basic cards."""
    questions = _quiz_rows(payload)
    pairs: list[tuple[str, str]] = []
    for row in questions:
        if not isinstance(row, Mapping):
            continue
        question = str(row.get("question") or "")
        options = row.get("answerOptions") or row.get("options") or []
        correct = [
            str(opt.get("text") or "")
            for opt in options
            if isinstance(opt, Mapping) and opt.get("isCorrect")
        ]
        hint = str(row.get("hint") or "").strip()
        back_parts = correct or [str(row.get("answer") or "")]
        if hint:
            back_parts.append(f"Hint: {hint}")
        pairs.append((question, "\n".join(part for part in back_parts if part)))
    return cards_from_qa_pairs(
        pairs,
        notebook_id=notebook_id,
        extra_tags=extra_tags,
        source_titles=source_titles,
        source_ref_prefix="quiz",
        cloze=cloze,
    )


def cards_from_transcript(
    payload: Mapping[str, Any],
    *,
    extra_tags: Sequence[str] = (),
    cloze: bool = False,
) -> tuple[AnkiCard, ...]:
    """Map a notebook transcript fixture (Q&A + notes + guides + artifacts)."""
    notebook_id = _optional_str(payload.get("notebook_id") or payload.get("id"))
    source_titles = [
        str(item.get("title"))
        for item in payload.get("sources") or ()
        if isinstance(item, Mapping) and item.get("title")
    ]
    cards: list[AnkiCard] = []
    qa = payload.get("qa_pairs") or payload.get("history") or ()
    if qa:
        cards.extend(
            cards_from_qa_pairs(
                list(qa),
                notebook_id=notebook_id,
                extra_tags=extra_tags,
                source_titles=source_titles,
                cloze=cloze,
            )
        )
    notes = payload.get("notes") or ()
    if notes:
        cards.extend(
            cards_from_notes(
                list(notes),
                notebook_id=notebook_id,
                extra_tags=extra_tags,
                source_titles=source_titles,
                cloze=cloze,
            )
        )
    for guide in payload.get("guides") or ():
        if not isinstance(guide, Mapping):
            continue
        cards.extend(
            cards_from_guide(
                str(guide.get("summary") or ""),
                keywords=tuple(guide.get("keywords") or ()),
                notebook_id=notebook_id,
                source_id=_optional_str(guide.get("source_id")),
                source_title=_optional_str(guide.get("source_title") or guide.get("title")),
                extra_tags=extra_tags,
                cloze=cloze,
            )
        )
    if payload.get("cards") or payload.get("flashcards"):
        cards.extend(
            cards_from_flashcards(
                payload,
                notebook_id=notebook_id,
                extra_tags=extra_tags,
                source_titles=source_titles,
                cloze=cloze,
            )
        )
    if payload.get("questions") or payload.get("quiz"):
        cards.extend(
            cards_from_quiz(
                payload,
                notebook_id=notebook_id,
                extra_tags=extra_tags,
                source_titles=source_titles,
                cloze=cloze,
            )
        )
    return tuple(cards)


def build_export(
    cards: Iterable[AnkiCard],
    *,
    notebook_id: str | None = None,
    deck: str | None = None,
    source_kind: str = "file",
) -> AnkiExport:
    """Wrap converted cards in the export envelope."""
    return AnkiExport(
        cards=tuple(cards),
        notebook_id=notebook_id,
        deck=deck,
        source_kind=source_kind,
    )


def _mapping_get(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _qa_pairs_from_text(content: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for match in _QA_BLOCK_RE.finditer(content):
        question = match.group(1).strip()
        answer = match.group(2).strip()
        if question and answer:
            pairs.append((question, answer))
    return pairs


def _heading_sections(markdown: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(markdown))
    if not matches:
        return []
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        if body:
            sections.append((match.group(1).strip(), body))
    return sections


def _keyword_context(summary: str, keyword: str) -> str:
    for line in summary.splitlines():
        if keyword.casefold() in line.casefold() and line.strip():
            return line.strip().strip("-* ")
    return ""


def _flashcard_rows(payload: Mapping[str, Any] | Sequence[Any]) -> Sequence[Any]:
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return payload
    for key in ("cards", "flashcards"):
        rows = payload.get(key)
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            return rows
    return ()


def _quiz_rows(payload: Mapping[str, Any] | Sequence[Any]) -> Sequence[Any]:
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return payload
    for key in ("questions", "quiz"):
        rows = payload.get(key)
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            return rows
    return ()
