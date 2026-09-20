"""Pure-function tests for the NotebookLM → Anki converter."""

from __future__ import annotations

import json
from pathlib import Path

from notebooklm.anki import (
    cards_from_flashcards,
    cards_from_guide,
    cards_from_notes,
    cards_from_qa_pairs,
    cards_from_quiz,
    cards_from_transcript,
    parse_export_payload,
    serialize_export,
)
from notebooklm.anki.convert import AnkiCard, build_export

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "anki" / "notebook_transcript.json"


def test_qa_pairs_become_basic_cards_with_notebook_tags() -> None:
    cards = cards_from_qa_pairs(
        [("What is SRS?", "Spaced repetition scheduling.")],
        notebook_id="nb_demo",
        extra_tags=("FOM",),
        source_titles=("Skript 1",),
    )
    assert len(cards) == 1
    card = cards[0]
    assert card.front == "What is SRS?"
    assert card.back == "Spaced repetition scheduling."
    assert card.note_type == "basic"
    assert card.tags == ("notebooklm", "nb:nb_demo", "Skript_1", "FOM")
    assert card.source_ref == "notebooklm:nb_demo#history:1"


def test_empty_or_whitespace_pairs_are_dropped() -> None:
    cards = cards_from_qa_pairs([(" ", "answer"), ("question", ""), ("Q", "A")])
    assert [card.front for card in cards] == ["Q"]


def test_existing_cloze_markup_sets_note_type() -> None:
    cards = cards_from_qa_pairs(
        [("The {{c1::hippocampus}} consolidates.", "Medial temporal lobe.")]
    )
    assert cards[0].note_type == "cloze"
    assert "{{c1::hippocampus}}" in cards[0].text


def test_cloze_flag_converts_bold_spans() -> None:
    cards = cards_from_qa_pairs(
        [("The **hippocampus** consolidates episodes.", "Medial temporal lobe.")],
        cloze=True,
    )
    assert cards[0].note_type == "cloze"
    assert cards[0].text == "The {{c1::hippocampus}} consolidates episodes."


def test_notes_explode_qa_blocks_and_fall_back_to_title_body() -> None:
    notes = [
        {
            "id": "n1",
            "title": "Turn note",
            "content": "**Q:** Front one\n\n**A:** Back one\n\n**Q:** Front two\n\n**A:** Back two",
        },
        {"id": "n2", "title": "Free note", "content": "A short definition."},
    ]
    cards = cards_from_notes(notes, notebook_id="nb1")
    assert [card.front for card in cards] == ["Front one", "Front two", "Free note"]
    assert cards[2].back == "A short definition."
    assert cards[0].source_ref.startswith("notebooklm:nb1#note:n1:")


def test_guide_headings_and_keywords_become_cards() -> None:
    cards = cards_from_guide(
        "## Working memory\n\nHolds a few items for seconds.\n\n## Interference\n\nOld material can block new material.",
        keywords=("Consolidation",),
        notebook_id="nb1",
        source_id="src1",
        source_title="Skript",
    )
    fronts = [card.front for card in cards]
    assert "Working memory" in fronts
    assert "Interference" in fronts
    assert "What is Consolidation?" in fronts
    assert all("Skript" in card.tags for card in cards)


def test_flashcards_accept_notebooklm_and_short_keys() -> None:
    cards = cards_from_flashcards(
        {"title": "Deck", "cards": [{"front": "Q1", "back": "A1"}, {"f": "Q2", "b": "A2"}]}
    )
    assert [(card.front, card.back) for card in cards] == [("Q1", "A1"), ("Q2", "A2")]


def test_quiz_uses_correct_options_and_hint() -> None:
    cards = cards_from_quiz(
        {
            "questions": [
                {
                    "question": "Which store is durable?",
                    "answerOptions": [
                        {"text": "Sensory", "isCorrect": False},
                        {"text": "Long-term memory", "isCorrect": True},
                    ],
                    "hint": "Hours to decades",
                }
            ]
        }
    )
    assert cards[0].front == "Which store is durable?"
    assert "Long-term memory" in cards[0].back
    assert "Hint: Hours to decades" in cards[0].back


def test_transcript_fixture_builds_history_note_and_guide_cards() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cards = cards_from_transcript(payload)
    fronts = [card.front for card in cards]
    assert "Was unterscheidet sensorisches Gedaechtnis und Arbeitsgedaechtnis?" in fronts
    assert "Welche Rolle hat der Hippocampus bei der Konsolidierung?" in fronts
    assert "Langzeitgedaechtnis" in fronts
    assert "Interferenz" in fronts
    assert all("notebooklm" in card.tags for card in cards)
    assert all("nb:nb_allg_psych_demo" in card.tags for card in cards)
    export = build_export(cards, notebook_id="nb_allg_psych_demo", deck="FOM::Allg Psych")
    yaml_text = serialize_export(export, "yaml")
    assert yaml_text.startswith("- Front:")
    assert "Tags:" in yaml_text
    tsv = serialize_export(export, "tsv")
    assert tsv.splitlines()[0] == "#separator:tab"
    assert "Basic" in tsv


def test_parse_history_json_envelope() -> None:
    parsed = parse_export_payload(
        json.dumps(
            {
                "notebook_id": "nb9",
                "qa_pairs": [{"question": "Q", "answer": "A"}],
            }
        )
    )
    assert parsed.detected_kind == "history"
    assert parsed.notebook_id == "nb9"
    assert parsed.cards[0].front == "Q"


def test_parse_markdown_qa_without_json() -> None:
    parsed = parse_export_payload("**Q:** Definition?\n\n**A:** A short answer.")
    assert parsed.cards[0].front == "Definition?"
    assert parsed.cards[0].back == "A short answer."


def test_serialize_jsonl_uses_lokallern_schema() -> None:
    export = build_export(
        [
            AnkiCard(
                front="Q",
                back="A",
                tags=("notebooklm",),
                source_ref="notebooklm:nb#history:1",
            )
        ],
        notebook_id="nb",
        deck="Demo",
    )
    line = serialize_export(export, "jsonl").strip()
    row = json.loads(line)
    assert row["schema"] == "lokallern.card.v1"
    assert row["status"] == "draft"
    assert row["deck"] == "Demo"
    assert row["front"] == "Q"
