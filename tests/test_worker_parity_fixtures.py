#!/usr/bin/env python3
"""The Worker's parity fixtures must match what the Python MCP server answers today.

worker/test/parity/fixtures.json holds the Python server's answers on fake data.
The Worker's vitest suite requires the TypeScript port to reproduce them. This
test regenerates the file in memory and fails if the committed copy is stale, so
a change to the Python rules cannot land without refreshing the fixtures (and,
through them, the Worker). Regenerate with:

    python3 worker/test/parity/record.py

No network, no credentials. Secret-shaped strings stay placeholders on disk.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "worker" / "test" / "parity" / "record.py"
FIXTURES = ROOT / "worker" / "test" / "parity" / "fixtures.json"


def load_recorder():
    spec = importlib.util.spec_from_file_location("egregore_worker_parity_record", RECORDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class WorkerParityFixturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.recorder = load_recorder()
        cls.committed = json.loads(FIXTURES.read_text(encoding="utf-8"))

    def test_committed_fixtures_match_the_python_server(self) -> None:
        fresh = json.loads(self.recorder.render(self.recorder.build()))
        self.assertEqual(
            fresh,
            self.committed,
            "worker/test/parity/fixtures.json is stale. Run: python3 worker/test/parity/record.py",
        )

    def test_committed_file_is_byte_for_byte_the_recorder_output(self) -> None:
        self.assertEqual(
            self.recorder.render(self.recorder.build()),
            FIXTURES.read_text(encoding="utf-8"),
        )

    def test_fixtures_hold_no_secret_shaped_text(self) -> None:
        text = FIXTURES.read_text(encoding="utf-8")
        for needle in ("ghp_", "sk-bbbb", "BEGIN RSA PRIVATE KEY", "BEGIN PRIVATE KEY"):
            self.assertNotIn(needle, text)

    def test_every_read_tool_is_covered(self) -> None:
        tools = {case["tool"] for case in self.committed["cases"]}
        self.assertEqual(
            tools,
            {"get_tasks", "get_task", "get_task_history", "get_owner_decisions", "get_specialists"},
        )

    def test_read_tool_list_matches_the_python_server(self) -> None:
        import asyncio
        import sys

        sys.path.insert(0, str(ROOT / "mcp"))
        from mcp import Client

        from egregore_mcp.server import build_server

        async def names() -> set[str]:
            service = self.recorder.CoordinationService(
                self.recorder.FixtureStore({"issues": [], "comments": {}}),
                "cursor",
                repo=self.recorder.REPO,
                auth_mode="github_app",
            )
            async with Client(build_server(service)) as client:
                return {tool.name for tool in (await client.list_tools()).tools}

        read_tools = {case["tool"] for case in self.committed["cases"]}
        self.assertTrue(read_tools <= asyncio.run(names()))


if __name__ == "__main__":
    unittest.main()
