#!/usr/bin/env python3
"""MCP protocol tests. Two clients speak stdio to the server and a fake GitHub."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "tests"))

from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

from fake_github import FakeGitHub, serve  # noqa: E402

TOKEN = "test-token"
BOUNDARY = (
    "Coordination only. No project data, health data, credentials, "
    "or another person's personal information."
)


def task_body() -> str:
    return "\n".join(
        [
            "### Project",
            "",
            "coordination",
            "",
            "### Role",
            "",
            "any",
            "",
            "### Priority",
            "",
            "normal",
            "",
            "### Outcome",
            "",
            "Two clients share one task without overwriting the lease.",
            "",
            "### Context",
            "",
            "https://github.com/AStrasler/Egeregore",
            "",
            "### Out of scope",
            "",
            "No live token.",
            "",
            "### Approval needed",
            "",
            "no",
            "",
            "### Data boundary",
            "",
            f"- [x] {BOUNDARY}",
            "",
        ]
    )


def parameters(agent: str, api: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "egregore_mcp"],
        cwd=str(ROOT),
        env={
            "PYTHONPATH": str(ROOT / "mcp"),
            "EGREGORE_AGENT": agent,
            "EGREGORE_GITHUB_TOKEN": TOKEN,
            "EGREGORE_GITHUB_API": api,
            "EGREGORE_REPO": "AStrasler/Egeregore",
            "EGREGORE_TRANSPORT": "stdio",
        },
    )


def payload(result: object) -> dict[str, object]:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None) or []
    text = getattr(content[0], "text", "")
    return json.loads(text)


class ProtocolTests(unittest.TestCase):
    def test_stdio_lists_tools_and_reads_a_task(self) -> None:
        store = FakeGitHub()
        number = store.seed_issue(
            title="[Task]: Protocol check",
            body=task_body(),
            labels=["type:task", "status:ready", "project:coordination"],
        )

        async def run(api: str) -> None:
            async with Client(parameters("cursor", api), read_timeout_seconds=30) as client:
                listed = await client.list_tools()
                names = [tool.name for tool in listed.tools]
                self.assertEqual(
                    names,
                    [
                        "get_tasks",
                        "get_task",
                        "claim_task",
                        "renew_lease",
                        "release_task",
                        "post_progress",
                        "handoff_task",
                        "request_review",
                        "get_owner_decisions",
                        "get_task_history",
                "get_specialists",
                    ],
                )
                tasks = payload(await client.call_tool("get_tasks", {}))
                self.assertTrue(tasks["ok"], tasks)
                self.assertEqual(tasks["result"]["tasks"][0]["number"], number)
                one = payload(await client.call_tool("get_task", {"issue_number": number}))
                self.assertEqual(one["result"]["sections"]["Role"], "any")
                bad = await client.call_tool("claim_task", {"issue_number": "nope"})
                self.assertTrue(bad.is_error)

        with serve(store, TOKEN) as api:
            asyncio.run(run(api))

    def test_two_clients_claim_progress_and_handoff(self) -> None:
        store = FakeGitHub()
        number = store.seed_issue(
            title="[Task]: Shared lease",
            body=task_body(),
            labels=["type:task", "status:ready", "project:coordination", "priority:normal"],
        )

        async def run(api: str) -> None:
            async with (
                Client(parameters("cursor", api), read_timeout_seconds=30) as cursor,
                Client(parameters("codex", api), read_timeout_seconds=30) as codex,
            ):
                claimed = payload(await cursor.call_tool("claim_task", {"issue_number": number, "lease_hours": 6}))
                self.assertTrue(claimed["ok"], claimed)
                self.assertEqual(claimed["result"]["lease"]["agent"], "cursor")
                lease_id = claimed["result"]["lease"]["lease_id"]

                seen = payload(await codex.call_tool("get_task", {"issue_number": number}))
                self.assertEqual(seen["result"]["lease"]["status"], "active")
                self.assertEqual(seen["result"]["lease"]["agent"], "cursor")
                self.assertEqual(seen["result"]["lease"]["lease_id"], lease_id)

                lost = payload(await codex.call_tool("claim_task", {"issue_number": number}))
                self.assertFalse(lost["ok"])
                self.assertEqual(lost["error"]["code"], "lease_conflict")
                self.assertEqual(lost["error"]["details"]["holder"], "cursor")
                self.assertEqual(lost["error"]["details"]["lease_id"], lease_id)
                self.assertIn("agent:cursor", store.label_names(number))
                self.assertNotIn("agent:codex", store.label_names(number))

                progress = payload(
                    await cursor.call_tool(
                        "post_progress",
                        {"issue_number": number, "done": "Claim is recorded.", "next": "Hand off to Codex."},
                    )
                )
                self.assertTrue(progress["ok"], progress)
                handed = payload(
                    await cursor.call_tool(
                        "handoff_task",
                        {
                            "issue_number": number,
                            "to_agent": "codex",
                            "done_so_far": "Cursor claimed the task and posted progress.",
                            "next_action": "Read the handoff and continue the review.",
                            "title": "Continue from Cursor",
                        },
                    )
                )
                self.assertTrue(handed["ok"], handed)
                handoff_number = handed["result"]["handoff_issue"]
                read = payload(await codex.call_tool("get_task", {"issue_number": handoff_number}))
                self.assertTrue(read["ok"], read)
                self.assertEqual(read["result"]["sections"]["From"], "cursor")
                self.assertEqual(read["result"]["sections"]["To"], "codex")
                self.assertIn("does not grant", read["result"]["sections"]["Access"])
                claimed_handoff = payload(await codex.call_tool("claim_task", {"issue_number": handoff_number}))
                self.assertTrue(claimed_handoff["ok"], claimed_handoff)
                self.assertEqual(claimed_handoff["result"]["lease"]["agent"], "codex")
                self.assertIn("status:claimed", store.label_names(handoff_number))
                history = payload(await codex.call_tool("get_task_history", {"issue_number": number}))
                kinds = [event["kind"] for event in history["result"]["events"]]
                self.assertIn("progress", kinds)
                self.assertIn("handoff", kinds)

        with serve(store, TOKEN) as api:
            asyncio.run(run(api))

    def test_streamable_http_lists_tools(self) -> None:
        store = FakeGitHub()
        store.seed_issue(
            title="[Task]: HTTP check",
            body=task_body(),
            labels=["type:task", "status:ready"],
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        with serve(store, TOKEN) as api:
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "PYTHONPATH": str(ROOT / "mcp"),
                "EGREGORE_AGENT": "cursor",
                "EGREGORE_GITHUB_TOKEN": TOKEN,
                "EGREGORE_GITHUB_API": api,
                "EGREGORE_REPO": "AStrasler/Egeregore",
                "EGREGORE_TRANSPORT": "streamable-http",
                "EGREGORE_HTTP_HOST": "127.0.0.1",
                "EGREGORE_HTTP_PORT": str(port),
            }
            process = subprocess.Popen(
                [sys.executable, "-m", "egregore_mcp"],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                self._wait_for_port(port)
                asyncio.run(self._http_round_trip(port))
            finally:
                process.terminate()
                process.wait(timeout=10)

    async def _http_round_trip(self, port: int) -> None:
        async with Client(f"http://127.0.0.1:{port}/mcp", read_timeout_seconds=20) as client:
            listed = await client.list_tools()
            self.assertIn("get_tasks", [tool.name for tool in listed.tools])
            tasks = payload(await client.call_tool("get_tasks", {}))
            self.assertTrue(tasks["ok"], tasks)
            self.assertEqual(tasks["result"]["count"], 1)

    def _wait_for_port(self, port: int) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        self.fail(f"streamable HTTP server did not listen on {port}")


if __name__ == "__main__":
    unittest.main()
