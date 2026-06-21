from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from backend.api.chat import ChatRequest, generate_chat_events, ndjson_event
from backend.core import storage
from backend.core.agent_loop import parse_tool_calls, run_agent_loop
from backend.core.agent_tools import (
    AgentToolError,
    build_call,
    consume_pending_approval,
    read_local_file,
    register_pending_approval,
    write_local_file,
)
from backend.core.config import get_settings
from backend.core.llm import ChatMessage, ChatStreamChunk


class ConfigTests(unittest.TestCase):
    def test_qwen_is_default_model(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OLLAMA_MODEL", None)
            get_settings.cache_clear()
            self.assertEqual(get_settings().ollama_model, "qwen2.5:7b")


class StorageTests(unittest.TestCase):
    def test_message_tool_metadata_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original_path = storage.DB_PATH
            storage.DB_PATH = Path(directory) / "test.sqlite3"
            try:
                storage.init_db()
                chat = storage.create_chat("Test")
                message = storage.create_message(
                    chat["id"],
                    "assistant",
                    "Done",
                    tools=[{"name": "file.read", "status": "completed"}],
                    sources=[{"title": "Example", "url": "https://example.com"}],
                )
                self.assertEqual(message["tools"][0]["name"], "file.read")
                self.assertEqual(message["sources"][0]["url"], "https://example.com")
            finally:
                storage.DB_PATH = original_path


class FileToolTests(unittest.TestCase):
    def test_csv_and_json_are_summarized(self) -> None:
        root = Path("tmp_test_agent_tools")
        root.mkdir(exist_ok=True)
        csv_path = root / "sample.csv"
        json_path = root / "sample.json"
        try:
            csv_path.write_text("name,score\nAda,98\n", encoding="utf-8")
            json_path.write_text('{"name":"Ada","score":98}', encoding="utf-8")
            self.assertIn("1 data rows", read_local_file(str(csv_path)).content)
            self.assertIn("object with 2 keys", read_local_file(str(json_path)).content)
        finally:
            csv_path.unlink(missing_ok=True)
            json_path.unlink(missing_ok=True)
            root.rmdir()

    def test_csv_json_and_pdf_edits(self) -> None:
        root = Path("tmp_test_agent_edits")
        root.mkdir(exist_ok=True)
        csv_path = root / "sample.csv"
        json_path = root / "sample.json"
        pdf_path = root / "sample.pdf"
        try:
            write_local_file(str(csv_path), "name,score\nAda,98", "replace")
            write_local_file(str(csv_path), "Grace,95", "append")
            self.assertIn("Grace,95", csv_path.read_text(encoding="utf-8"))

            write_local_file(str(json_path), '{"name":"Ada"}', "replace")
            write_local_file(str(json_path), '{"score":98}', "merge")
            self.assertIn('"score": 98', json_path.read_text(encoding="utf-8"))

            write_local_file(str(pdf_path), "First PDF page", "replace")
            write_local_file(str(pdf_path), "Second PDF page", "append")
            pdf_text = read_local_file(str(pdf_path)).content
            self.assertIn("First PDF page", pdf_text)
            self.assertIn("Second PDF page", pdf_text)
        finally:
            csv_path.unlink(missing_ok=True)
            json_path.unlink(missing_ok=True)
            pdf_path.unlink(missing_ok=True)
            root.rmdir()


class ApprovalTests(unittest.TestCase):
    def test_approval_is_one_time(self) -> None:
        call = register_pending_approval(
            build_call("command.run", {"command": "echo hello"}, requires_confirmation=True)
        )
        self.assertEqual(consume_pending_approval(call.id).arguments["command"], "echo hello")
        with self.assertRaises(AgentToolError):
            consume_pending_approval(call.id)


class AgentLoopTests(unittest.TestCase):
    def test_parses_ollama_tool_call(self) -> None:
        calls = parse_tool_calls(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "file_read", "arguments": {"path": "README.md"}}}
                ],
            }
        )
        self.assertEqual(calls[0].name, "file.read")

    @patch("backend.core.agent_loop.chat_with_ollama_tools")
    def test_model_can_choose_safe_tool(self, mock_chat) -> None:
        mock_chat.side_effect = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "file_read", "arguments": {"path": "README.md"}}}
                ],
            },
            {"role": "assistant", "content": "enough"},
        ]
        outcome = run_agent_loop(
            [ChatMessage(role="user", content="Inspect the project file README.md")],
            model="qwen2.5:7b",
            temperature=0.2,
        )
        self.assertEqual(outcome.tool_results[0].name, "file.read")
        self.assertEqual(outcome.tool_results[0].status, "completed")

    @patch("backend.core.agent_loop.chat_with_ollama_tools")
    def test_model_file_write_requires_confirmation(self, mock_chat) -> None:
        mock_chat.return_value = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "file_write",
                        "arguments": {
                            "path": "data.json",
                            "content": '{"ready":true}',
                            "mode": "replace",
                        },
                    }
                }
            ],
        }
        outcome = run_agent_loop(
            [ChatMessage(role="user", content="Write data.json with a ready flag")],
            model="qwen2.5:7b",
            temperature=0.2,
        )
        self.assertIsNotNone(outcome.pending_call)
        self.assertEqual(outcome.pending_call.name, "file.write")
        self.assertTrue(outcome.pending_call.requires_confirmation)


class StreamTests(unittest.TestCase):
    def test_ndjson_event(self) -> None:
        self.assertEqual(ndjson_event("token", content="Hi"), '{"type": "token", "content": "Hi"}\n')

    @patch("backend.api.chat.stream_chat_with_ollama")
    def test_length_limited_stream_continues_once(self, mock_stream) -> None:
        mock_stream.side_effect = [
            iter(
                [
                    ChatStreamChunk(content="First", done=False),
                    ChatStreamChunk(done=True, done_reason="length"),
                ]
            ),
            iter(
                [
                    ChatStreamChunk(content=" second", done=False),
                    ChatStreamChunk(done=True, done_reason="stop"),
                ]
            ),
        ]
        events = "".join(
            generate_chat_events(
                ChatRequest(
                    messages=[ChatMessage(role="user", content="Explain something")],
                    model="qwen2.5:7b",
                    max_tokens=32,
                )
            )
        )
        self.assertIn('"type": "continuation"', events)
        self.assertIn('"content": "First"', events)
        self.assertIn('"content": " second"', events)

    @patch("backend.api.chat.stream_chat_with_ollama")
    def test_approved_file_write_does_not_ask_again(self, mock_stream) -> None:
        path = Path("tmp_approved_write.json")
        call = register_pending_approval(
            build_call(
                "file.write",
                {"path": str(path), "content": '{"done":true}', "mode": "replace"},
                requires_confirmation=True,
            )
        )
        try:
            events = "".join(
                generate_chat_events(
                    ChatRequest(
                        messages=[ChatMessage(role="user", content="Write the JSON file")],
                        model="qwen2.5:7b",
                        max_tokens=32,
                        approved_tool_call={
                            "id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                        },
                    )
                )
            )
            self.assertIn("Done. Updated local file", events)
            self.assertNotIn("Would you like to proceed", events)
            self.assertTrue(path.exists())
            mock_stream.assert_not_called()
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
