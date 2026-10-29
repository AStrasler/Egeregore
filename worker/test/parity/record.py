#!/usr/bin/env python3
"""Record the Python MCP server's answers on fake GitHub data.

`python3 worker/test/parity/record.py` rewrites fixtures.json. The Worker's
vitest suite (test/parity.test.ts) replays the same data through the TypeScript
port and requires an identical answer for every case. A Python unit test
(tests/test_worker_parity_fixtures.py) regenerates the fixtures and fails if the
committed file is stale, so the two servers cannot drift apart silently.

Each case goes through the real Python MCP server over an in-memory client, so
the recorded value is exactly what a client sees: structuredContent and the
indent-2 JSON text. No network. No credentials. Secret-shaped strings are
written as {{ghp}}, {{sk}} and {{pem}} placeholders and expanded at run time on
both sides, so this file and fixtures.json never hold one.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "scripts"))

from mcp import Client  # noqa: E402

from egregore_mcp.github_api import GitHubError  # noqa: E402
from egregore_mcp.server import build_server  # noqa: E402
from egregore_mcp.service import CoordinationService  # noqa: E402

FIXTURES = HERE / "fixtures.json"
REPO = "AStrasler/Egeregore"
NOW = "2026-10-27T18:00:00Z"
BOT = "egregore[bot]"
OWNER = "AStrasler"

# Placeholders for secret-shaped text. Built from pieces so no literal appears here.
SUBSTITUTIONS = {
    "{{ghp}}": "ghp_" + "a" * 36,
    "{{sk}}": "sk-" + "b" * 30,
    "{{pem}}": "-----BEGIN " + "RSA PRIVATE KEY-----",
}


def expand(value):
    """Replace placeholders in every string of a JSON-shaped value."""
    if isinstance(value, str):
        for key, secret in SUBSTITUTIONS.items():
            value = value.replace(key, secret)
        return value
    if isinstance(value, list):
        return [expand(item) for item in value]
    if isinstance(value, dict):
        return {key: expand(item) for key, item in value.items()}
    return value


def task_body(role: str, project: str = "coordination", extra: str = "") -> str:
    return "\n".join(
        [
            "### Project",
            "",
            project,
            "",
            "### Role",
            "",
            role,
            "",
            "### Priority",
            "",
            "normal",
            "",
            "### Outcome",
            "",
            "Something useful.",
            "",
            "### Context",
            "",
            "Because.",
            "",
            "### Out of scope",
            "",
            "Everything else.",
            "",
            "### Approval needed",
            "",
            "No",
            "",
            "### Data boundary",
            "",
            "- [x] Coordination only. No project data, health data, credentials, or another person's personal information.",
            "",
            extra,
        ]
    )


def issue(number, title, body, labels, *, state="open", created="2026-10-20T09:00:00Z", pull=False):
    data = {
        "number": number,
        "title": title,
        "body": body,
        "state": state,
        "labels": labels,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": created,
    }
    if pull:
        data["pull_request"] = {"url": f"https://api.github.com/repos/{REPO}/pulls/{number}"}
    return data


def label(name):
    return {"name": name}


def comment(cid, created, body, login=BOT):
    return {
        "id": cid,
        "created_at": created,
        "updated_at": created,
        "body": body,
        "user": {"login": login},
    }


def claim(agent, claimed, expires):
    return (
        f"Agent: {agent.title()}\n\n## Claim\nClaim-Agent: {agent}\nClaimed-At: {claimed}\n"
        f"Lease-Expires: {expires}\nTask: work\nData-Boundary: coordination-only\n"
    )


def build_dataset() -> dict:
    issues = []
    comments: dict[str, list] = {}

    # 1. Live cursor lease with an overlapping claim, a foreign release, a renewal.
    issues.append(
        issue(1, "[Task]: Live lease", task_body("cursor"),
              [label("type:task"), label("status:in-progress"), label("project:coordination"), label("priority:high")])
    )
    comments["1"] = [
        comment(1001, "2026-10-27T15:00:00Z", claim("cursor", "2026-10-27T15:00:00Z", "2026-10-27T21:00:00Z")),
        comment(1002, "2026-10-27T15:30:00Z", claim("codex", "2026-10-27T15:30:00Z", "2026-10-27T20:00:00Z")),
        comment(1003, "2026-10-27T15:45:00Z", "Agent: Codex\n\n## Lease release\nClaim-Agent: codex\nLease-Release: codex\n"),
        comment(1004, "2026-10-27T16:00:00Z", "Agent: Cursor\n\n## Progress\nClaim-Agent: cursor\nDone: a\nNext: b\n"),
        comment(1005, "2026-10-27T17:00:00Z", "Agent: Cursor\n\n## Lease renewal\nLease-Expires: 2026-10-27T22:30:00Z\nNote: more time\n"),
        comment(1006, "2026-10-27T17:10:00Z", "<!-- egregore-hygiene -->\n\nCoordination field check passed.\n"),
        comment(1007, "not-a-time", "Agent: Grokbot\n\nHello with a bad timestamp.", login="someone"),
        comment(1008, "2026-10-27T17:20:00Z", "<!-- egregore-stall:needs-review:2026-10-27T10:00:00Z -->\n\nStalled."),
        comment(1009, "2026-10-27T17:21:00Z", "<!-- egregore-lease-conflict:abc -->\n\nOverlapping claim ignored."),
        comment(1010, "2026-10-27T17:22:00Z", "Agent: Cursor\n\n## Evidence\nSee link."),
        comment(1011, "2026-10-27T17:23:00Z", "<!-- egregore-lease:active:1001 -->\n\nBackstop."),
        comment(1012, "2026-10-27T17:24:00Z", "plain words only", login="AStrasler"),
    ]

    # 2. Expired grokbot lease.
    issues.append(
        issue(2, "[Task]: Expired lease", task_body("grokbot", "whoop-lens"),
              [label("type:task"), label("status:claimed"), label("project:whoop-lens")])
    )
    comments["2"] = [comment(2001, "2026-10-26T10:00:00Z", claim("grokbot", "2026-10-26T10:00:00Z", "2026-10-26T12:00:00Z"))]

    # 3. A lease longer than 24 hours is refused.
    issues.append(issue(3, "[Task]: Too long", task_body("any"), [label("type:task"), label("status:ready")]))
    comments["3"] = [comment(3001, "2026-10-27T09:00:00Z", claim("cursor", "2026-10-27T09:00:00Z", "2026-10-29T09:00:00Z"))]

    # 4. A handoff that was claimed, released, and left for review.
    handoff = "\n".join(
        ["### Project", "", "coordination", "", "### From", "", "cursor", "", "### To", "", "Codex", "",
         "### Related issue", "", "#1", "", "### Done so far", "", "x", "", "### Next action", "", "y", "",
         "### Access", "", "This handoff does not grant access.", ""]
    )
    issues.append(issue(4, "[Handoff]: Pass it on", handoff,
                        [label("type:handoff"), label("status:needs-review"), label("agent:codex")]))
    comments["4"] = [
        comment(4001, "2026-10-27T11:00:00Z", "Agent: Cursor\n\n## Handoff\nClaim-Agent: cursor\nLease-Release: cursor\nTo: codex\n"),
        comment(4002, "2026-10-27T12:00:00Z", claim("codex", "2026-10-27T12:00:00Z", "2026-10-27T14:00:00Z")),
        comment(4003, "2026-10-27T13:00:00Z", "Agent: Codex\n\n## Lease release\nClaim-Agent: codex\nLease-Release: codex\n"),
    ]

    # 5. An owner decision with several Decision comments.
    issues.append(issue(5, "[Decision]: Ship it?", "### Project\n\ncoordination\n\n### Decision type\n\nmerge\n",
                        [label("type:decision"), label("status:needs-owner")]))
    comments["5"] = [
        comment(5001, "2026-10-26T10:00:00Z", "Decision: approve\n\nship it", login=OWNER),
        comment(5002, "2026-10-26T10:05:00Z", "decision: REJECT.", login="someone"),
        comment(5003, "2026-10-26T10:06:00Z", "Decision: approved by me", login=BOT),
        comment(5004, "2026-10-26T10:07:00Z", "\n\n   Decision:   reject\r\nreason", login=OWNER),
        comment(5005, "2026-10-26T10:08:00Z", "<!-- egregore-decision:recorded:approve:5001 -->\n\nRecorded decision only.", login=BOT),
        comment(5006, "2026-10-26T10:09:00Z", "Thanks.", login="someone"),
        {"id": 0, "created_at": "2026-10-26T10:10:00Z", "body": "Decision: approve", "user": None},
        {"created_at": "2026-10-26T10:11:00Z", "body": "Decision: reject"},
    ]

    # 6. A closed decision with no comments, and a task that is a decision by label.
    issues.append(issue(6, "[Decision]: Closed one", "### Project\n\ncoordination\n", [label("type:decision")],
                        state="closed", created="2026-10-10T09:00:00Z"))
    comments["6"] = []
    issues.append(issue(11, "[Task]: Decision by label", task_body("aaron"),
                        [label("type:decision"), label("type:task"), label("status:needs-owner")]))
    comments["11"] = [comment(11001, "2026-10-27T10:00:00Z", "Decision: reject", login=OWNER)]

    # 7. A pull request. Every list skips it.
    issues.append(issue(7, "[Task]: this is a pull request", task_body("cursor"), [label("type:task")], pull=True))
    comments["7"] = [comment(7001, "2026-10-27T10:00:00Z", "Looks fine.")]

    # 8. Secret-shaped text in the body and in comments.
    issues.append(issue(8, "[Task]: Has a secret", task_body("cursor", extra="Token: {{ghp}}\n{{pem}}\n"),
                        [label("type:task"), label("status:needs-owner")]))
    comments["8"] = [
        comment(8001, "2026-10-27T10:00:00Z", "Agent: Cursor\n\nI saw {{sk}} in a log and {{pem}}"),
    ]

    # 9. Formatting that separates Python string handling from JavaScript's.
    odd = (
        "### Role\r\n\r\n  Cursor\x1cjunk  \r\n\r\n### 2024\r\nyear\r\n### __proto__\r\nhello\r\n"
        "### R\u00e9sum\u00e9\u00a0\r\ntext\u2028second\r\n### Project\r\nweather-intelligence\u2028other\r\n"
        "### Role\r\nlater duplicate\r\n"
    )
    issues.append(issue(9, " [TASK]: Odd formatting \u00e9", odd, [label("type:task"), "status:ready", {"name": None}, {"nope": 1}, 7]))
    comments["9"] = [
        comment(9001, "2026-10-27T10:00:00Z",
                "Claim-Agent: Cursor\r\nLease-Expires:\u00a02026-10-27T20:00:00Z\r\n"),
        comment(9002, "2026-10-27T12:00:00+0000", "```\nClaim-Agent: cursor\n```\n"),
        comment(9003, "2026-10-27T12:00:00.5Z", "Lease-Expires: 2026-10-27T21:00:00Z"),
        comment(9004, "2026-10-27T12:00:00", "Naive timestamp"),
        {"id": 9005, "created_at": None, "body": "No timestamp", "user": {"login": "x"}},
        comment(9006, "2026-10-27T12:01:00Z",
                "Claim-Agent: cursor\nLease-Expires: \u0662\u0660\u0662\u0666-\u0661\u0660-\u0662\u0667T\u0662\u0660:\u0660\u0660:\u0660\u0660Z\n"),
        comment(9007, "2026-10-27T12:02:00Z", "Claim-Agent: robot\nLease-Expires: 2026-10-27T21:00:00Z\n"),
        comment(9008, "2026-10-27T12:03:00Z", "line one\rAgent:   Grokbot  \rline three"),
        comment(9009, "2026-10-27T12:04:00Z", "agent:"),
        comment(9010, "2026-10-27T12:05:00Z", "\u2028Lease-Release: cursor\u2028"),
    ]

    # 10. A long thread that needs two pages of comments.
    issues.append(issue(10, "[Task]: Long thread", task_body("cursor"), [label("type:task")]))
    long_thread = [comment(10001, "2026-10-27T14:00:00Z", claim("cursor", "2026-10-27T14:00:00Z", "2026-10-27T20:00:00Z"))]
    for index in range(1, 120):
        minute = index % 60
        hour = 14 + index // 60
        long_thread.append(
            comment(10001 + index, f"2026-10-27T{hour:02d}:{minute:02d}:30Z",
                    f"Agent: Cursor\n\n## Progress\nClaim-Agent: cursor\nDone: step {index}\nNext: step {index + 1}\n")
        )
    comments["10"] = long_thread

    # 12. Evidence and review kinds.
    issues.append(issue(12, "[Evidence]: Proof", "### Project\n\ncoordination\n", [label("type:evidence")]))
    comments["12"] = []
    issues.append(issue(13, "[Review]: Look at this", "### Project\n\ncoordination\n", [label("type:review")],
                        state="closed"))
    comments["13"] = []
    issues.append(issue(14, "No prefix at all", "just words", []))
    comments["14"] = []

    return {"issues": issues, "comments": comments}


class FixtureStore:
    """The read methods RestGitHub has, over the dataset."""

    def __init__(self, dataset: dict) -> None:
        self.issues = {item["number"]: item for item in dataset["issues"]}
        self.order = [item["number"] for item in dataset["issues"]]
        self.comments = dataset["comments"]

    def list_issues(self, *, state: str):
        rows = [self.issues[number] for number in self.order]
        if state != "all":
            rows = [row for row in rows if row["state"] == state]
        return rows

    def get_issue(self, number: int):
        if number not in self.issues:
            raise GitHubError(404, "GitHub HTTP 404: Not Found")
        return self.issues[number]

    def list_comments(self, number: int):
        if number not in self.issues:
            raise GitHubError(404, "GitHub HTTP 404: Not Found")
        return list(self.comments.get(str(number), []))


def case(name, tool, args=None, agent="cursor"):
    return {"name": name, "tool": tool, "args": args or {}, "agent": agent}


CASES = [
    case("tasks default", "get_tasks"),
    case("tasks as grokbot", "get_tasks", agent="grokbot"),
    case("tasks all states and kinds", "get_tasks", {"state": "all", "kind": "all"}),
    case("tasks empty kind means every kind", "get_tasks", {"kind": ""}),
    case("tasks closed", "get_tasks", {"state": "closed", "kind": "all"}),
    case("tasks handoff kind", "get_tasks", {"kind": "handoff"}),
    case("tasks decision kind", "get_tasks", {"kind": "decision", "state": "all"}),
    case("tasks status short", "get_tasks", {"status": "claimed"}),
    case("tasks status prefixed", "get_tasks", {"status": "status:in-progress"}),
    case("tasks project", "get_tasks", {"project": "coordination"}),
    case("tasks project prefixed by section", "get_tasks", {"project": "project:weather-intelligence"}),
    case("tasks project by label", "get_tasks", {"project": "whoop-lens"}),
    case("tasks role", "get_tasks", {"role": "cursor"}),
    case("tasks role padded and upper", "get_tasks", {"role": " Grokbot "}),
    case("tasks limit 2", "get_tasks", {"limit": 2}),
    case("tasks limit 1 all", "get_tasks", {"limit": 1, "kind": "all", "state": "all"}),
    case("tasks bad state", "get_tasks", {"state": "bogus"}),
    case("tasks bad kind", "get_tasks", {"kind": "bogus"}),
    case("tasks limit zero", "get_tasks", {"limit": 0}),
    case("tasks limit 101", "get_tasks", {"limit": 101}),
    case("task live lease", "get_task", {"issue_number": 1}),
    case("task expired lease", "get_task", {"issue_number": 2}),
    case("task lease too long", "get_task", {"issue_number": 3}),
    case("task handoff released", "get_task", {"issue_number": 4}, agent="codex"),
    case("task decision", "get_task", {"issue_number": 5}),
    case("task secret redacted", "get_task", {"issue_number": 8}),
    case("task odd formatting", "get_task", {"issue_number": 9}),
    case("task long thread", "get_task", {"issue_number": 10}),
    case("task is a pull request", "get_task", {"issue_number": 7}),
    case("task missing", "get_task", {"issue_number": 999}),
    case("task zero", "get_task", {"issue_number": 0}),
    case("task negative", "get_task", {"issue_number": -4}),
    case("history live lease", "get_task_history", {"issue_number": 1}),
    case("history decision", "get_task_history", {"issue_number": 5}),
    case("history handoff", "get_task_history", {"issue_number": 4}),
    case("history secret redacted", "get_task_history", {"issue_number": 8}),
    case("history odd formatting", "get_task_history", {"issue_number": 9}),
    case("history long thread", "get_task_history", {"issue_number": 10}),
    case("history missing", "get_task_history", {"issue_number": 999}),
    case("history zero", "get_task_history", {"issue_number": 0}),
    case("decisions default", "get_owner_decisions"),
    case("decisions all", "get_owner_decisions", {"state": "all"}),
    case("decisions closed", "get_owner_decisions", {"state": "closed"}),
    case("decisions limit 1", "get_owner_decisions", {"state": "all", "limit": 1}),
    case("decisions bad state", "get_owner_decisions", {"state": "bogus"}),
    case("decisions limit zero", "get_owner_decisions", {"limit": 0}),
    case("specialists all", "get_specialists"),
    case("specialists by role", "get_specialists", {"role": "code-reviewer"}),
    case("specialists by kind", "get_specialists", {"kind": "skill"}),
    case("specialists by id", "get_specialists", {"specialist_id": "ponytail"}),
    case("specialists no match", "get_specialists", {"role": "no-such-role"}),
    case("specialists bad slug", "get_specialists", {"role": "Not A Slug"}),
    case("specialists bad kind", "get_specialists", {"kind": "prompt"}),
]


async def _record(dataset: dict) -> list[dict]:
    live = expand(dataset)
    now = datetime.fromisoformat(NOW.replace("Z", "+00:00")).astimezone(timezone.utc)
    cases = []
    for item in CASES:
        service = CoordinationService(
            FixtureStore(live), item["agent"], repo=REPO, now=lambda: now, auth_mode="github_app"
        )
        async with Client(build_server(service)) as client:
            result = await client.call_tool(item["tool"], item["args"])
        text = result.content[0].text
        structured = result.structured_content
        assert json.loads(text) == structured, item["name"]
        sections = (structured.get("result") or {}).get("sections") or {}
        cases.append(
            {
                **item,
                "expected": structured,
                "text": text,
                # JavaScript objects order integer-like keys first. Python keeps document order.
                "text_exact": not any(key.isdigit() for key in sections),
            }
        )
    return cases


def build() -> dict:
    dataset = build_dataset()
    return {
        "note": "Generated by worker/test/parity/record.py from the Python MCP server. Do not edit.",
        "now": NOW,
        "repo": REPO,
        "dataset": dataset,
        "cases": asyncio.run(_record(dataset)),
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=True) + "\n"


def main() -> None:
    FIXTURES.write_text(render(build()), encoding="utf-8")
    print(f"wrote {FIXTURES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
