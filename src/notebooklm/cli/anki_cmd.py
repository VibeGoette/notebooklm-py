"""``notebooklm anki`` — export NotebookLM study material as Anki cards."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

import click

from .._app.chat import fetch_history
from .._app.source_content import SourceGuidePlan, execute_source_guide
from ..anki import (
    AnkiExport,
    build_export,
    cards_from_flashcards,
    cards_from_guide,
    cards_from_notes,
    cards_from_qa_pairs,
    cards_from_quiz,
    parse_export_payload,
    serialize_export,
)
from ..anki.serialize import ExportFormat, card_as_dict
from .auth_runtime import resolve_client_factory, run_client_workflow
from .error_handler import _output_error
from .input import read_stdin_text
from .options import json_option, notebook_option, output_option
from .rendering import cli_print, json_output_response
from .resolve import require_notebook, resolve_notebook_id, resolve_source_id

if TYPE_CHECKING:
    from ..client import NotebookLMClient

_SOURCE_KINDS = ("file", "history", "notes", "guide", "flashcards", "quiz")
_FORMATS = ("yaml", "tsv", "json", "jsonl")


@click.group()
def anki() -> None:
    """Export NotebookLM Q&A, notes, guides, and flashcards as Anki cards.

    \b
    Commands:
      export  Convert NotebookLM study material into Anki / anki-maxed files
    """


@anki.command("export")
@notebook_option
@output_option
@json_option
@click.option(
    "--from",
    "source_kind",
    type=click.Choice(_SOURCE_KINDS, case_sensitive=True),
    default="file",
    show_default=True,
    help="Card source. ``file`` is offline; the others fetch after login.",
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Local JSON/JSONL/Markdown to convert (``-`` reads stdin). Required for --from file.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(_FORMATS, case_sensitive=True),
    default="yaml",
    show_default=True,
    help="yaml = anki-llm import; tsv = Anki text import; json/jsonl = lokallern ingest.",
)
@click.option("--deck", default=None, help="Deck name stored in JSON/JSONL metadata.")
@click.option(
    "--tag",
    "extra_tags",
    multiple=True,
    help="Extra Anki tag (repeatable). notebooklm + notebook id are always added.",
)
@click.option(
    "--source",
    "source_id",
    default=None,
    help="Source ID for --from guide. Supports partial IDs.",
)
@click.option(
    "--source-title",
    default=None,
    help="Optional source title tag when converting a local file.",
)
@click.option(
    "--cloze",
    is_flag=True,
    default=False,
    help="Turn **bold** spans into Anki cloze deletions when present.",
)
@click.option("--limit", default=100, show_default=True, help="Max history turns (--from history).")
@click.option(
    "--artifact",
    "artifact_id",
    default=None,
    help="Flashcard/quiz artifact ID (latest if omitted).",
)
@click.pass_context
def anki_export(
    ctx: click.Context,
    notebook_id: str | None,
    output_path: str | None,
    json_output: bool,
    source_kind: str,
    input_path: Path | None,
    output_format: ExportFormat,
    deck: str | None,
    extra_tags: tuple[str, ...],
    source_id: str | None,
    source_title: str | None,
    cloze: bool,
    limit: int,
    artifact_id: str | None,
) -> None:
    """Convert NotebookLM material into Anki-importable cards.

    \b
    Offline (no Google auth):
      notebooklm anki export --from file --input history.json -o cards.yaml

    \b
    After ``notebooklm login``:
      notebooklm anki export --from history -n <nb> -o cards.yaml
      notebooklm anki export --from notes -n <nb> --format jsonl -o cards.jsonl
      notebooklm anki export --from flashcards -n <nb> --format tsv -o cards.tsv
    """
    _validate_export_flags(source_kind, input_path, source_id)
    if source_kind == "file":
        export = _export_from_file(
            input_path,
            extra_tags=extra_tags,
            cloze=cloze,
            notebook_id=notebook_id,
            source_title=source_title,
            deck=deck,
        )
    else:
        export = _export_from_live(
            ctx,
            source_kind=source_kind,
            notebook_id=notebook_id,
            extra_tags=extra_tags,
            cloze=cloze,
            deck=deck,
            source_id=source_id,
            limit=limit,
            artifact_id=artifact_id,
            json_output=json_output,
        )
    _emit_export(export, output_format, output_path, json_output)


def _validate_export_flags(
    source_kind: str, input_path: Path | None, source_id: str | None
) -> None:
    if source_kind == "file" and input_path is None:
        raise click.UsageError(  # cli-input-validation: file export needs --input
            "--from file requires --input PATH (or --input - for stdin)."
        )
    if source_kind == "guide" and not source_id:
        raise click.UsageError(  # cli-input-validation: guide export needs --source
            "--from guide requires --source SOURCE_ID."
        )


def _export_from_file(
    input_path: Path | None,
    *,
    extra_tags: tuple[str, ...],
    cloze: bool,
    notebook_id: str | None,
    source_title: str | None,
    deck: str | None,
) -> AnkiExport:
    raw = _read_input(input_path)
    parsed = parse_export_payload(
        raw,
        kind="file",
        extra_tags=extra_tags,
        cloze=cloze,
        notebook_id=notebook_id,
        source_title=source_title,
    )
    return build_export(
        parsed.cards,
        notebook_id=parsed.notebook_id,
        deck=deck,
        source_kind=parsed.detected_kind,
    )


def _read_input(input_path: Path | None) -> str:
    if input_path is None or str(input_path) == "-":
        return read_stdin_text(source_label="anki export input")
    try:
        return input_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(  # cli-input-validation: input file must be readable
            f"Cannot read {input_path}: {exc}"
        ) from exc


def _export_from_live(
    ctx: click.Context,
    *,
    source_kind: str,
    notebook_id: str | None,
    extra_tags: tuple[str, ...],
    cloze: bool,
    deck: str | None,
    source_id: str | None,
    limit: int,
    artifact_id: str | None,
    json_output: bool,
) -> AnkiExport:
    async def body(client: NotebookLMClient) -> AnkiExport:
        nb_id = await resolve_notebook_id(
            client, require_notebook(notebook_id), json_output=json_output
        )
        titles = await _source_titles(client, nb_id)
        cards: tuple[Any, ...]
        if source_kind == "history":
            fetched = await fetch_history(client, nb_id, limit=limit)
            cards = cards_from_qa_pairs(
                fetched.qa_pairs,
                notebook_id=nb_id,
                extra_tags=extra_tags,
                source_titles=titles,
                cloze=cloze,
            )
        elif source_kind == "notes":
            notes = await client.notes.list(nb_id)
            cards = cards_from_notes(
                notes,
                notebook_id=nb_id,
                extra_tags=extra_tags,
                source_titles=titles,
                cloze=cloze,
            )
        elif source_kind == "guide":
            resolved_source = await resolve_source_id(
                client, nb_id, source_id or "", json_output=json_output
            )
            guide = await execute_source_guide(
                client, SourceGuidePlan(notebook_id=nb_id, source_id=resolved_source)
            )
            cards = cards_from_guide(
                guide.summary,
                keywords=guide.keywords,
                notebook_id=nb_id,
                source_id=guide.source_id,
                extra_tags=extra_tags,
                cloze=cloze,
            )
        elif source_kind == "flashcards":
            payload = await _download_json_artifact(
                client, nb_id, "flashcards", artifact_id=artifact_id
            )
            cards = cards_from_flashcards(
                payload,
                notebook_id=nb_id,
                extra_tags=extra_tags,
                source_titles=titles,
                cloze=cloze,
            )
        else:
            payload = await _download_json_artifact(client, nb_id, "quiz", artifact_id=artifact_id)
            cards = cards_from_quiz(
                payload,
                notebook_id=nb_id,
                extra_tags=extra_tags,
                source_titles=titles,
                cloze=cloze,
            )
        return build_export(cards, notebook_id=nb_id, deck=deck, source_kind=source_kind)

    return run_client_workflow(
        ctx,
        command_name="anki_export",
        json_output=json_output,
        body=body,
        client_factory=resolve_client_factory(ctx),
    )


async def _source_titles(client: NotebookLMClient, notebook_id: str) -> tuple[str, ...]:
    sources = await client.sources.list(notebook_id)
    return tuple(source.title for source in sources if getattr(source, "title", None))


async def _download_json_artifact(
    client: NotebookLMClient,
    notebook_id: str,
    kind: str,
    *,
    artifact_id: str | None,
) -> dict[str, Any]:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{kind}.json"
        if kind == "flashcards":
            await client.artifacts.download_flashcards(
                notebook_id, str(path), artifact_id=artifact_id, output_format="json"
            )
        else:
            await client.artifacts.download_quiz(
                notebook_id, str(path), artifact_id=artifact_id, output_format="json"
            )
        loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        return loaded
    return {"items": loaded}


def _emit_export(
    export: AnkiExport,
    output_format: ExportFormat,
    output_path: str | None,
    json_output: bool,
) -> None:
    if export.count == 0:
        _output_error(
            "No cards could be built from the selected NotebookLM material.",
            "NOT_FOUND",
            json_output,
            1,
        )
    rendered = serialize_export(export, output_format)
    if output_path:
        Path(output_path).write_text(rendered, encoding="utf-8")
    if json_output:
        json_output_response(
            {
                "count": export.count,
                "format": output_format,
                "output_path": output_path,
                "notebook_id": export.notebook_id,
                "deck": export.deck,
                "source_kind": export.source_kind,
                "cards": [
                    card_as_dict(card, deck=export.deck, notebook_id=export.notebook_id)
                    for card in export.cards
                ],
            }
        )
        return
    if output_path:
        cli_print(f"[green]Wrote {export.count} cards to {output_path}[/green]")
        return
    click.echo(rendered, nl=False)
