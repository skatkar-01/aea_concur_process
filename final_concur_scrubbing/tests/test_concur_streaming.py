"""
tests/test_concur_streaming.py
--------------------------------
Unit tests for streamed OpenAI responses in concur_extractor.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.concur_extractor import ContentFilterRefusalError, _call_api


def _make_stream(events, final_response):
    stream = MagicMock()
    stream.__enter__.return_value = stream
    stream.__exit__.return_value = False
    stream.__iter__.return_value = iter(events)
    stream.get_final_response.return_value = final_response
    return stream


def test_streaming_call_collects_deltas():
    client = MagicMock()
    final_response = MagicMock(finish_reason="stop")
    stream = _make_stream(
        [
            SimpleNamespace(type="response.output_text.delta", delta='{"ok":'),
            SimpleNamespace(type="response.output_text.delta", delta=" true}"),
        ],
        final_response,
    )
    client.responses.stream.return_value = stream

    output_text, response = _call_api(client, "file-123", "gpt-test", "sample.pdf", 16000)

    assert output_text == '{"ok": true}'
    assert response is final_response
    client.responses.stream.assert_called_once()


def test_streaming_call_raises_on_refusal_phrase(tmp_path: Path):
    client = MagicMock()
    final_response = MagicMock(finish_reason="stop")
    stream = _make_stream(
        [
            SimpleNamespace(type="response.output_text.delta", delta="I cannot assist "),
            SimpleNamespace(type="response.output_text.delta", delta="with that request."),
        ],
        final_response,
    )
    client.responses.stream.return_value = stream

    with patch("src.concur_extractor.get_settings", return_value=SimpleNamespace(log_dir=tmp_path / "logs")):
        with pytest.raises(ContentFilterRefusalError):
            _call_api(client, "file-123", "gpt-test", "sample.pdf", 16000)


def test_streaming_call_reports_missing_completed_event(tmp_path: Path):
    client = MagicMock()
    stream = _make_stream(
        [
            SimpleNamespace(type="response.created", delta=""),
            SimpleNamespace(type="response.output_text.delta", delta='{"ok": true}'),
        ],
        MagicMock(),
    )
    stream.get_final_response.side_effect = RuntimeError("Didn't receive a `response.completed` event.")
    client.responses.stream.return_value = stream

    with patch("src.concur_extractor.get_settings", return_value=SimpleNamespace(log_dir=tmp_path / "logs")):
        with pytest.raises(RuntimeError) as exc:
            _call_api(client, "file-123", "gpt-test", "sample.pdf", 16000)

    message = str(exc.value)
    assert "response.completed" in message
    assert "events_seen=2" in message
    detail_files = list((tmp_path / "logs" / "stream_errors").glob("*.json"))
    assert len(detail_files) == 1
    detail_data = json.loads(detail_files[0].read_text(encoding="utf-8"))
    assert detail_data["event_count"] == 2
    assert detail_data["events"] == ["response.created", "response.output_text.delta"]
