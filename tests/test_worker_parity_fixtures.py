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


READ_TOOLS = {"get_tasks", "get_task", "get_task_history", "get_owner_decisions", "get_specialists"}
WRITE_TOOLS = {"claim_task", "renew_lease", "release_task", "post_progress", "handoff_task", "request_review"}


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

    def test_every_write_tool_is_exercised_by_a_story(self) -> None:
        tools = {step["tool"] for story in self.committed["stories"] for step in story["steps"]}
        self.assertLessEqual(WRITE_TOOLS, tools)

    def test_every_write_tool_has_a_success_and_a_refusal_on_record(self) -> None:
        for name in WRITE_TOOLS:
            steps = [s for story in self.committed["stories"] for s in story["steps"] if s["tool"] == name]
            self.assertTrue(any(s["expected"]["ok"] for s in steps), f"{name} has no recorded success")
            self.assertTrue(any(not s["expected"]["ok"] for s in steps), f"{name} has no recorded refusal")

    def test_a_refused_write_never_wrote(self) -> None:
        # Refusals that the Python server makes before any post must stay writes-free in the Worker too.
        writes = {"create_comment", "create_issue", "add_labels", "remove_label"}
        for story in self.committed["stories"]:
            for step in story["steps"]:
                code = (step["expected"].get("error") or {}).get("code")
                if code in {
                    "invalid_input",
                    "secret_rejected",
                    "forbidden",
                    "not_found",
                    "not_claimable",
                    "wrong_role",
                    "lease_not_holder",
                    "lease_expired",
                    "not_done",
                    "not_reviewable",
                    "review_current",
                    "review_pending",
                    "review_duplicate",
                    "review_same_head",
                    "review_limit",
                }:
                    made = [call["op"] for call in step["calls"] if call["op"] in writes]
                    self.assertEqual(made, [], f"{story['name']}: {step['tool']} {code} wrote {made}")

    def test_tool_list_matches_the_python_server(self) -> None:
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

        recorded = {tool["name"] for tool in self.committed["tools"]}
        self.assertEqual(recorded, asyncio.run(names()))
        self.assertEqual(recorded, READ_TOOLS | WRITE_TOOLS)


if __name__ == "__main__":
    unittest.main()
