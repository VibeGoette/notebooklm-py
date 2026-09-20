"""CLI tests for ``notebooklm anki export`` (offline file path + help)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

import notebooklm.auth as auth_module
from notebooklm.cli import helpers as helpers_module
from notebooklm.notebooklm_cli import cli

from .conftest import create_mock_client, inject_client

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "anki" / "notebook_transcript.json"


@pytest.fixture
def mock_auth():
    with patch.object(helpers_module, "load_auth_from_storage") as mock:
        mock.return_value = {
            "SID": "test",
            "HSID": "test",
            "SSID": "test",
            "APISID": "test",
            "SAPISID": "test",
        }
        yield mock


def test_anki_help_lists_export() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["anki", "--help"])
    assert result.exit_code == 0
    assert "export" in result.output


def test_anki_export_from_fixture_writes_yaml(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "cards.yaml"
    result = runner.invoke(
        cli,
        [
            "anki",
            "export",
            "--from",
            "file",
            "--input",
            str(FIXTURE),
            "--deck",
            "FOM::Allg Psych",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    text = output.read_text(encoding="utf-8")
    assert text.startswith("- Front:")
    assert "Tags:" in text
    assert "Wrote" in result.output


def test_anki_export_json_envelope_includes_cards(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "cards.jsonl"
    result = runner.invoke(
        cli,
        [
            "anki",
            "export",
            "--from",
            "file",
            "--input",
            str(FIXTURE),
            "--format",
            "jsonl",
            "-o",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["count"] >= 4
    assert payload["format"] == "jsonl"
    assert payload["cards"][0]["schema"] == "lokallern.card.v1"
    assert output.read_text(encoding="utf-8").count("\n") == payload["count"]


def test_anki_export_file_requires_input() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["anki", "export", "--from", "file"])
    assert result.exit_code != 0
    assert "--input" in result.output


def test_anki_export_empty_file_exits_not_found(tmp_path: Path) -> None:
    runner = CliRunner()
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    result = runner.invoke(
        cli,
        ["anki", "export", "--from", "file", "--input", str(empty), "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"] is True
    assert payload["code"] == "NOT_FOUND"


def test_anki_export_reads_stdin() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["anki", "export", "--from", "file", "--input", "-", "--format", "json"],
        input=json.dumps({"qa_pairs": [{"question": "Q?", "answer": "A."}]}),
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["cards"][0]["front"] == "Q?"
    assert payload["cards"][0]["back"] == "A."


def test_anki_export_from_history_uses_client(tmp_path: Path, mock_auth) -> None:
    runner = CliRunner()
    mock_client = create_mock_client()

    @asynccontextmanager
    async def _operation(*_args, **_kwargs):
        yield mock_client

    mock_client.operation = _operation
    mock_client.chat.get_conversation_id = AsyncMock(return_value="conv_1")
    mock_client.chat.get_history = AsyncMock(
        return_value=[("What is interference?", "Old material blocking new.")]
    )
    output = tmp_path / "history.yaml"
    with patch.object(auth_module, "fetch_tokens_with_domains", new_callable=AsyncMock) as fetch:
        fetch.return_value = ("csrf", "session")
        result = runner.invoke(
            cli,
            [
                "anki",
                "export",
                "--from",
                "history",
                "-n",
                "nb_123",
                "-o",
                str(output),
                "--json",
            ],
            obj=inject_client(mock_client),
        )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["source_kind"] == "history"
    assert payload["cards"][0]["front"] == "What is interference?"
    assert "notebooklm" in payload["cards"][0]["tags"]
    assert output.read_text(encoding="utf-8").startswith("- Front:")
