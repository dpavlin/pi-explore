#!/usr/bin/env python3
"""
Unit and integration tests for pi-explore.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import importlib.util
spec = importlib.util.spec_from_file_location("pi_explore", str(Path(__file__).parent.parent / "pi-explore.py"))
pie = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pie)


class TestPiExploreFormatting(unittest.TestCase):
    def test_encode_cwd(self):
        self.assertEqual(pie.encode_cwd("/home/dpavlin"), "--home-dpavlin--")
        self.assertEqual(pie.encode_cwd("/home/dpavlin/pi-explore"), "--home-dpavlin-pi-explore--")

    def test_format_duration(self):
        self.assertEqual(pie.format_duration(45), "45s")
        self.assertEqual(pie.format_duration(150), "2m 30s")
        self.assertEqual(pie.format_duration(3660), "1h 1m")
        self.assertEqual(pie.format_duration(90000), "1d 1h")

    def test_format_size(self):
        self.assertEqual(pie.format_size(500), "500 B")
        self.assertEqual(pie.format_size(2048), "2.0 KB")
        self.assertEqual(pie.format_size(1048576 * 3), "3.0 MB")

    def test_format_tokens(self):
        self.assertEqual(pie.format_tokens(500), "500")
        self.assertEqual(pie.format_tokens(1500), "1.5k")
        self.assertEqual(pie.format_tokens(1500000), "1.50M")

    def test_format_cost(self):
        self.assertEqual(pie.format_cost(0.0), "$0.00")
        self.assertEqual(pie.format_cost(0.0035), "$0.0035")
        self.assertEqual(pie.format_cost(1.25), "$1.25")

    def test_is_text_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write("Hello world!\nLine 2\n")
            tf_path = tf.name
        try:
            self.assertTrue(pie.is_text_file(tf_path))
        finally:
            os.remove(tf_path)

        with tempfile.NamedTemporaryFile("wb", delete=False) as bf:
            bf.write(b"Binary\x00Data\xff")
            bf_path = bf.name
        try:
            self.assertFalse(pie.is_text_file(bf_path))
        finally:
            os.remove(bf_path)

    def test_highlight_text(self):
        text = "Refactor auth logic"
        hl = pie.highlight_text(text, ["auth"], highlight_color="[H]", context_color="[C]")
        self.assertIn(f"[H]auth{pie.CLR_RESET}[C]", hl)


class TestPiExploreTreeAndPath(unittest.TestCase):
    def test_linear_session_path(self):
        entries = [
            {"id": "root", "parentId": None, "type": "session"},
            {"id": "m1", "parentId": "root", "type": "message"},
            {"id": "m2", "parentId": "m1", "type": "message"}
        ]
        path = pie.build_active_path(entries)
        self.assertEqual([e["id"] for e in path], ["root", "m1", "m2"])

    def test_branched_session_path(self):
        entries = [
            {"id": "root", "parentId": None, "type": "session"},
            {"id": "turn1", "parentId": "root", "type": "message"},
            {"id": "abandoned_branch", "parentId": "turn1", "type": "message"},
            {"id": "active_branch", "parentId": "turn1", "type": "message"},
            {"id": "active_leaf", "parentId": "active_branch", "type": "message"}
        ]
        path = pie.build_active_path(entries)
        self.assertEqual([e["id"] for e in path], ["root", "turn1", "active_branch", "active_leaf"])


class TestPiExploreSessionProcessing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = tempfile.TemporaryDirectory()
        self.session_file = Path(self.temp_dir.name) / "test_session.jsonl"

        # Create a mock session JSONL file
        lines = [
            {"type": "session", "version": 3, "id": "019d-test-uuid-1234", "cwd": self.workspace_dir.name, "timestamp": "2026-10-11T10:00:00.000Z"},
            {"type": "model_change", "id": "mc1", "parentId": None, "provider": "lucebox", "modelId": "deepseek-v4-flash", "timestamp": "2026-10-11T10:00:01.000Z"},
            {"type": "message", "id": "msg1", "parentId": "mc1", "timestamp": "2026-10-11T10:00:02.000Z", "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Deploy nginx configuration to /etc/nginx"}]
            }},
            {"type": "message", "id": "msg2", "parentId": "msg1", "timestamp": "2026-10-11T10:00:05.000Z", "message": {
                "role": "assistant",
                "model": "deepseek-v4-flash",
                "provider": "lucebox",
                "usage": {"input": 100, "output": 50, "cacheRead": 20, "cacheWrite": 0, "totalTokens": 170, "cost": {"total": 0.001}},
                "content": [
                    {"type": "thinking", "thinking": "Analyzing nginx configuration deployment requirements."},
                    {"type": "text", "text": "Creating configuration file now."},
                    {"type": "toolCall", "id": "tc1", "name": "write", "arguments": {"path": "nginx.conf", "content": "server { listen 80; }"}}
                ]
            }},
            {"type": "message", "id": "msg3", "parentId": "msg2", "timestamp": "2026-10-11T10:00:06.000Z", "message": {
                "role": "toolResult",
                "toolCallId": "tc1",
                "toolName": "write",
                "durationMs": 42,
                "content": [{"type": "text", "text": "Wrote 24 bytes to nginx.conf"}]
            }}
        ]
        with open(self.session_file, "w", encoding="utf-8") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

    def tearDown(self):
        self.temp_dir.cleanup()
        self.workspace_dir.cleanup()

    def test_process_session_file(self):
        res = pie.process_session_file(str(self.session_file), show_all=True)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], "019d-test-uuid-1234")
        self.assertEqual(res["cwd"], self.workspace_dir.name)
        self.assertEqual(res["user_prompts"], ["Deploy nginx configuration to /etc/nginx"])
        self.assertEqual(res["models"], ["lucebox/deepseek-v4-flash"])
        self.assertEqual(res["tool_counts"]["write"], 1)
        self.assertEqual(res["thoughts"], 1)
        self.assertEqual(res["token_usage"]["totalTokens"], 170)
        self.assertAlmostEqual(res["cost"], 0.001)

    def test_grep_filtering(self):
        # Match word present
        res_match = pie.process_session_file(str(self.session_file), show_all=True, search_words=["nginx", "deploy"])
        self.assertIsNotNone(res_match)
        self.assertTrue(len(res_match["matched_snippets"]) > 0)

        # Word not present
        res_nomatch = pie.process_session_file(str(self.session_file), show_all=True, search_words=["apache"])
        self.assertIsNone(res_nomatch)

    def test_search_all_thoughts(self):
        # Searching for 'requirements' which is only in thoughts
        res_dialogue_only = pie.process_session_file(str(self.session_file), show_all=True, search_words=["requirements"], search_all=False)
        self.assertIsNone(res_dialogue_only)

        res_search_all = pie.process_session_file(str(self.session_file), show_all=True, search_words=["requirements"], search_all=True)
        self.assertIsNotNone(res_search_all)


class TestPiExploreCLIIntegration(unittest.TestCase):
    def test_cli_help(self):
        cmd = [sys.executable, str(Path(__file__).parent.parent / "pi-explore.py"), "--help"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Explore Pi Conversation Transcripts", res.stdout)
        self.assertIn("--grep", res.stdout)
        self.assertIn("--turn", res.stdout)

    def test_cli_list_all(self):
        cmd = [sys.executable, str(Path(__file__).parent.parent / "pi-explore.py"), "-a"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Analyzing Pi conversation session(s)", res.stdout)

    def test_cli_grep_query(self):
        cmd = [sys.executable, str(Path(__file__).parent.parent / "pi-explore.py"), "-a", "--grep", "mailman"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Filtered by --grep query: 'mailman'", res.stdout)
        self.assertIn("Session ID:", res.stdout)


if __name__ == "__main__":
    unittest.main()
