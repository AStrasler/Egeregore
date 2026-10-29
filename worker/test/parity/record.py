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

The write tools are recorded as stories: a small dataset and a list of tool
calls that run in order against one mutable store. Each step records the
envelope and the exact sequence of GitHub calls the Python server made (reads
and writes), so the Worker has to make the same calls in the same order, not just
return the same answer. A step may inject a GitHub failure for that step only.
"""

from __future__ import annotations

import asyncio
import copy
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
from egregore_mcp.redaction import redact_secrets  # noqa: E402
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


def _safe_error(status: int, text: str) -> str:
    """What RestGitHub puts in GitHubError.message for an error response."""
    lines = redact_secrets(text).strip().splitlines()
    return f"GitHub HTTP {status}: {(lines[0] if lines else 'GitHub request failed')[:300]}"


class FixtureStore:
    """The methods RestGitHub has, over the dataset. Writes mutate it and every call is logged."""

    def __init__(self, dataset: dict) -> None:
        self.issues = {item["number"]: item for item in dataset["issues"]}
        self.order = [item["number"] for item in dataset["issues"]]
        self.comments = dataset["comments"]
        self.pulls = {int(key): value for key, value in dataset.get("pulls", {}).items()}
        self.reactions = {int(key): value for key, value in dataset.get("reactions", {}).items()}
        self.log: list[dict] = []
        self.fault: dict | None = None
        self.drift: dict | None = None
        self.next_comment = 900001

    def _enter(self, op: str, **fields: object) -> None:
        self.log.append({"op": op, **fields})
        drift = self.drift
        if drift and drift["op"] == op:
            # Someone else changed the issue while this call was in flight. Once.
            self.issues[drift["number"]].update(drift["set"])
            self.drift = None
        fault = self.fault
        if fault and fault["op"] == op and fault.get("times", 1) > 0:
            if fault.get("skip", 0) > 0:
                fault["skip"] -= 1
                return
            fault["times"] = fault.get("times", 1) - 1
            if op == "remove_label" and fault["status"] == 404:
                self._skip_remove = True
                return
            raise GitHubError(fault["status"], _safe_error(fault["status"], fault["text"]))

    def list_issues(self, *, state: str):
        self._enter("list_issues", state=state)
        rows = [self.issues[number] for number in self.order]
        if state != "all":
            rows = [row for row in rows if row["state"] == state]
        return rows

    def get_issue(self, number: int):
        self._enter("get_issue", number=number)
        if number not in self.issues:
            raise GitHubError(404, "GitHub HTTP 404: Not Found")
        return self.issues[number]

    def list_comments(self, number: int):
        self._enter("list_comments", number=number)
        if number not in self.issues:
            raise GitHubError(404, "GitHub HTTP 404: Not Found")
        return list(self.comments.get(str(number), []))

    def get_pull(self, number: int):
        self._enter("get_pull", number=number)
        if number not in self.pulls:
            raise GitHubError(404, "GitHub HTTP 404: Not Found")
        return self.pulls[number]

    def list_reactions(self, number: int):
        self._enter("list_reactions", number=number)
        if number not in self.issues:
            raise GitHubError(404, "GitHub HTTP 404: Not Found")
        return list(self.reactions.get(number, []))

    def create_issue(self, *, title: str, body: str, labels: list[str]):
        self._enter("create_issue", title=title, body=body, labels=list(labels))
        number = max(self.issues) + 1
        created = issue(number, title, body, [label(name) for name in labels], created=NOW)
        self.issues[number] = created
        self.order.append(number)
        self.comments[str(number)] = []
        return created

    def create_comment(self, number: int, body: str):
        self._enter("create_comment", number=number, body=body)
        if number not in self.issues:
            raise GitHubError(404, "GitHub HTTP 404: Not Found")
        created = comment(self.next_comment, NOW, body)
        self.next_comment += 1
        self.comments.setdefault(str(number), []).append(created)
        return created

    def add_labels(self, number: int, labels: list[str]) -> None:
        if not labels:
            return
        self._enter("add_labels", number=number, labels=list(labels))
        row = self.issues[number]["labels"]
        have = {entry["name"] for entry in row if isinstance(entry, dict)}
        row.extend(label(name) for name in labels if name not in have)

    def remove_label(self, number: int, name: str) -> None:
        self._skip_remove = False
        self._enter("remove_label", number=number, name=name)
        if self._skip_remove:
            return
        row = self.issues[number]["labels"]
        row[:] = [entry for entry in row if not (isinstance(entry, dict) and entry.get("name") == name)]


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


# ---- Write stories -------------------------------------------------------

CODEX = "chatgpt-codex-connector[bot]"
HEAD_A = "a1" * 20
HEAD_B = "b2" * 20
HEAD_C = "c3" * 20
HEAD_D = "d4" * 20


def pr_issue(number):
    return issue(number, f"Pull request {number}", "", [], pull=True)


def pull(number, head=HEAD_A, *, state="open", merged=False):
    return {"number": number, "state": state, "merged": merged, "head": {"sha": head}}


def summary_comment(cid, created, sha, unresolved, status="completed"):
    payload = {"code_review": {"status": status, "commit_sha": sha, "has_unresolved_findings": unresolved}}
    return comment(cid, created, "**Codex review**\n\ncodex-review-state:v1 " + json.dumps(payload) + "\n", login=CODEX)


def receipt_comment(cid, created, sha, clean=True, login=CODEX):
    verdict = "Codex Review: Didn't find any major issues." if clean else "Codex Review: Found issues."
    return comment(cid, created, f"<!-- codex-review-receipt -->\n{verdict}\n\n**Reviewed commit:** `{sha}`\n", login=login)


def thumbs(login=CODEX):
    return {"content": "+1", "user": {"login": login}}


def held_by(agent, extra=()):
    return [
        comment(1, "2026-10-27T17:00:00Z", claim(agent, "2026-10-27T17:00:00Z", "2026-10-27T23:00:00Z")),
        *extra,
    ]


def story(name, issues, comments, steps, *, pulls=None, reactions=None):
    dataset = {"issues": issues, "comments": comments}
    if pulls:
        dataset["pulls"] = {str(key): value for key, value in pulls.items()}
    if reactions:
        dataset["reactions"] = {str(key): value for key, value in reactions.items()}
    return {"name": name, "dataset": dataset, "steps": steps}


def step(tool, args=None, agent="cursor", fault=None, drift=None):
    item = {"tool": tool, "args": args or {}, "agent": agent}
    if fault:
        item["fault"] = fault
    if drift:
        item["drift"] = drift
    return item


def fault(op, status, text, *, times=1, skip=0):
    return {"op": op, "status": status, "text": text, "times": times, "skip": skip}


def task(number, title, role="any", labels=None, *, state="open", body=None):
    chosen = labels if labels is not None else ["type:task", "status:ready"]
    return issue(number, f"[Task]: {title}", body if body is not None else task_body(role), [label(n) for n in chosen], state=state)


def lifecycle_story():
    issues = [task(1, "Lifecycle", labels=["type:task", "status:ready", "project:coordination", "priority:high"])]
    steps = [
        step("claim_task", {"issue_number": 1, "lease_hours": 6}),
        step("claim_task", {"issue_number": 1}),
        step("renew_lease", {"issue_number": 1, "lease_hours": 24, "note": "need the full day"}),
        step("post_progress", {"issue_number": 1, "done": "wired the thing", "next": "tests next"}),
        step("claim_task", {"issue_number": 1}, agent="grokbot"),
        step("claim_task", {"issue_number": 1, "lease_hours": 2}, agent="grokbot"),
        step("release_task", {"issue_number": 1, "status": "ready", "reason": "not mine"}, agent="grokbot"),
        step("get_task_history", {"issue_number": 1}),
        step("handoff_task", {"issue_number": 1, "to_agent": "Codex", "done_so_far": "built it", "next_action": "review it"}),
        step("get_task", {"issue_number": 1}),
        step("claim_task", {"issue_number": 2}, agent="codex"),
        step("post_progress", {"issue_number": 2, "done": "read it", "next": "comment on it"}, agent="codex"),
        step("release_task", {"issue_number": 2, "status": "status:needs-review", "reason": "ready for review"}, agent="codex"),
        step("release_task", {"issue_number": 2, "status": "done", "reason": "finished"}, agent="codex"),
        step("get_tasks", {"kind": "all", "state": "all"}),
    ]
    return story("lifecycle", issues, {"1": []}, steps)


def validation_story():
    issues = [
        task(20, "Plain ready"),
        pr_issue(21),
        task(22, "Closed", state="closed"),
        task(23, "Done", labels=["type:task", "status:done"]),
        task(24, "Blocked", labels=["type:task", "status:blocked"]),
        task(25, "Needs review", labels=["type:task", "status:needs-review"]),
        issue(26, "[Decision]: Not a task", "### Project\n\ncoordination\n", [label("type:decision"), label("status:ready")]),
        task(27, "Owner label", labels=["type:task", "status:needs-owner"]),
        task(28, "Approval yes", body=task_body("any").replace("### Approval needed\n\nNo", "### Approval needed\n\nYes, Aaron")),
        task(29, "For codex", role="codex"),
        task(30, "Expired hold", labels=["type:task", "status:claimed", "agent:grokbot", "lease:expired"]),
        task(31, "Held without lease", labels=["type:task", "status:in-progress"]),
        task(33, "Held by cursor", labels=["type:task", "status:in-progress", "agent:cursor"]),
        task(34, "Expired cursor", labels=["type:task", "status:claimed", "agent:cursor", "lease:expired"]),
        task(35, "Held but blocked", labels=["type:task", "status:blocked", "agent:cursor"]),
        task(36, "Held but closed", labels=["type:task", "status:in-progress"], state="closed"),
        task(37, "Held but owner", labels=["type:task", "status:needs-owner", "agent:cursor"]),
        task(38, "Held and done", labels=["type:task", "status:done", "agent:cursor"]),
    ]
    comments = {
        "30": [comment(3001, "2026-10-27T08:00:00Z", claim("grokbot", "2026-10-27T08:00:00Z", "2026-10-27T10:00:00Z"))],
        "33": held_by("cursor"),
        "34": [comment(3401, "2026-10-27T08:00:00Z", claim("cursor", "2026-10-27T08:00:00Z", "2026-10-27T10:00:00Z"))],
        "35": held_by("cursor"),
        "36": held_by("cursor"),
        "37": held_by("cursor"),
        "38": held_by("cursor"),
    }
    long_text = "x" * 4001
    exact_text = "y" * 4000
    steps = [
        step("claim_task", {"issue_number": 20, "lease_hours": 0}),
        step("claim_task", {"issue_number": 20, "lease_hours": 25}),
        step("claim_task", {"issue_number": 21}),
        step("claim_task", {"issue_number": 22}),
        step("claim_task", {"issue_number": 23}),
        step("claim_task", {"issue_number": 24}),
        step("claim_task", {"issue_number": 25}),
        step("claim_task", {"issue_number": 26}),
        step("claim_task", {"issue_number": 27}),
        step("claim_task", {"issue_number": 28}),
        step("claim_task", {"issue_number": 29}),
        step("claim_task", {"issue_number": 31}),
        step("claim_task", {"issue_number": 30}, agent="chatgpt"),
        step("claim_task", {"issue_number": 999}),
        step("claim_task", {"issue_number": 0}),
        step("claim_task", {"issue_number": -3}),
        step("renew_lease", {"issue_number": 20}),
        step("renew_lease", {"issue_number": 33, "lease_hours": 0}),
        step("renew_lease", {"issue_number": 33, "note": "has {{ghp}} inside"}),
        step("renew_lease", {"issue_number": 33, "note": "Decision: approve"}),
        step("renew_lease", {"issue_number": 33, "note": long_text}),
        step("renew_lease", {"issue_number": 33}, agent="grokbot"),
        step("renew_lease", {"issue_number": 34}),
        step("renew_lease", {"issue_number": 35}),
        step("renew_lease", {"issue_number": 36}),
        step("renew_lease", {"issue_number": 37}),
        step("renew_lease", {"issue_number": 38}),
        step("renew_lease", {"issue_number": 999}),
        step("renew_lease", {"issue_number": 0}),
        step("post_progress", {"issue_number": 33, "done": "", "next": "n"}),
        step("post_progress", {"issue_number": 33, "done": "d", "next": "_No response_"}),
        step("post_progress", {"issue_number": 33, "done": "   ", "next": "n"}),
        step("post_progress", {"issue_number": 33, "done": "d", "next": "has {{sk}} here"}),
        step("post_progress", {"issue_number": 33, "done": "{{pem}}", "next": "n"}),
        step("post_progress", {"issue_number": 33, "done": "  Decision:   reject\n", "next": "n"}),
        step("post_progress", {"issue_number": 33, "done": long_text, "next": "n"}),
        step("post_progress", {"issue_number": 33, "done": "d", "next": "n"}, agent="grokbot"),
        step("post_progress", {"issue_number": 34, "done": "d", "next": "n"}),
        step("post_progress", {"issue_number": 35, "done": "d", "next": "n"}),
        step("post_progress", {"issue_number": 999, "done": "d", "next": "n"}),
        step("post_progress", {"issue_number": 33, "done": exact_text, "next": "n"}),
        step("release_task", {"issue_number": 33, "status": "bogus", "reason": "r"}),
        step("release_task", {"issue_number": 33, "status": "status:in-progress", "reason": "r"}),
        step("release_task", {"issue_number": 33, "status": "ready", "reason": ""}),
        step("release_task", {"issue_number": 33, "status": "ready", "reason": "_No response_"}),
        step("release_task", {"issue_number": 33, "status": "ready", "reason": "token {{ghp}}"}),
        step("release_task", {"issue_number": 33, "status": "ready", "reason": "decision: approve"}),
        step("release_task", {"issue_number": 33, "status": "ready", "reason": "r"}, agent="grokbot"),
        step("release_task", {"issue_number": 20, "status": "ready", "reason": "r"}),
        step("release_task", {"issue_number": 35, "status": "ready", "reason": "r"}),
        step("release_task", {"issue_number": 36, "status": "ready", "reason": "r"}),
        step("release_task", {"issue_number": 38, "status": "needs-review", "reason": "r"}),
        step("release_task", {"issue_number": 999, "status": "ready", "reason": "r"}),
        step("release_task", {"issue_number": 37, "status": "needs-owner", "reason": "owner must decide"}),
        step("release_task", {"issue_number": 34, "status": "ready", "reason": "lease ran out"}),
        step("get_task", {"issue_number": 34}),
        step("handoff_task", {"issue_number": 33, "to_agent": "cursor", "done_so_far": "d", "next_action": "n"}),
        step("handoff_task", {"issue_number": 33, "to_agent": "robot", "done_so_far": "d", "next_action": "n"}),
        step("handoff_task", {"issue_number": 33, "to_agent": "", "done_so_far": "d", "next_action": "n"}),
        step("handoff_task", {"issue_number": 33, "to_agent": "codex", "done_so_far": "", "next_action": "n"}),
        step("handoff_task", {"issue_number": 33, "to_agent": "codex", "done_so_far": "d", "next_action": "_No response_"}),
        step("handoff_task", {"issue_number": 33, "to_agent": "codex", "done_so_far": "d", "next_action": "n", "title": "bad {{ghp}}"}),
        step("handoff_task", {"issue_number": 33, "to_agent": "codex", "done_so_far": "d", "next_action": "n"}, agent="codex"),
        step("handoff_task", {"issue_number": 34, "to_agent": "codex", "done_so_far": "d", "next_action": "n"}),
        step("handoff_task", {"issue_number": 35, "to_agent": "codex", "done_so_far": "d", "next_action": "n"}),
        step("handoff_task", {"issue_number": 999, "to_agent": "codex", "done_so_far": "d", "next_action": "n"}),
        step("get_tasks", {"kind": "all", "state": "all", "limit": 50}),
    ]
    return story("validation", issues, comments, steps)


def done_story():
    link = f"https://github.com/{REPO}/pull/"

    def held(number, title, body_extra="", labels=None, evidence=True, **kw):
        body = task_body("cursor", extra=body_extra, **kw)
        thread = held_by("cursor", [comment(2, "2026-10-27T17:05:00Z", "Agent: Cursor\n\n## Evidence\nShipped.")] if evidence else [])
        return task(number, title, labels=labels or ["type:task", "status:in-progress", "agent:cursor"], body=body), thread

    cases = {}
    cases[40] = held(40, "Merged and reviewed", f"PR: {link}50\n")
    cases[41] = held(41, "Not merged", f"PR: {link}51\n")
    cases[42] = held(42, "Unresolved findings", f"PR: {link}52\n")
    cases[43] = held(43, "No evidence", f"PR: {link}50\n", evidence=False)
    cases[44] = held(44, "Docs only", "", evidence=True)
    cases[45] = held(45, "Outside repo", "PR: https://github.com/someone/else/pull/9\n")
    cases[46] = held(46, "Unknown requirement", "")
    cases[47] = held(47, "Wants a pull request", "")
    cases[48] = held(48, "Missing reaction", f"PR: {link}53\n")
    cases[49] = held(49, "Copied payload", f"PR: {link}54\n")
    cases[60] = held(60, "Two pulls, second unreviewed", f"PR: {link}50 and {link}55\n")
    cases[61] = held(61, "Evidence by issue", "", evidence=False)
    cases[62] = held(62, "Needs owner", f"PR: {link}50\n", labels=["type:task", "status:needs-owner", "agent:cursor"])
    cases[63] = held(63, "Stale head", f"PR: {link}56\n")
    cases[64] = held(64, "Mention is not evidence", "", evidence=False)
    cases[65] = held(65, "Changed during the gate", "", evidence=False)
    issues = [cases[n][0] for n in sorted(cases)]
    # Task bodies that decide the pull-request rule are set after the fact.
    by_number = {item["number"]: item for item in issues}
    by_number[44]["body"] = task_body("cursor").replace("Something useful.", "Docs only, no pull request needed.")
    by_number[47]["body"] = task_body("cursor").replace("Because.", "This needs a pull request before it is done.")
    by_number[61]["body"] = task_body("cursor").replace("Something useful.", "Research only.")
    by_number[64]["body"] = task_body("cursor").replace("Something useful.", "Research only.")
    by_number[65]["body"] = task_body("cursor").replace("Something useful.", "Research only.")
    comments = {str(n): cases[n][1] for n in cases}
    # Evidence issue that names task 61 and an unrelated one that names 64 inside another number.
    issues.append(issue(70, "[Evidence]: for sixty-one", "### Project\n\ncoordination\n\n### Related issue\n\n#61\n", [label("type:evidence")]))
    comments["70"] = []
    issues.append(issue(71, "[Evidence]: for six hundred forty", "### Related issue\n\n#640 and #64a and x#64\n", [label("type:evidence")]))
    comments["71"] = []
    issues.append(issue(72, "A plain issue naming 64", f"See {REPO}#64 and #64\n", []))
    comments["72"] = []
    issues.append(issue(73, "[Evidence]: for sixty-five", "### Related issue\n\n#65\n", [label("type:evidence")]))
    comments["73"] = []
    for number in (50, 51, 52, 53, 54, 55, 56):
        issues.append(pr_issue(number))
    pulls = {
        50: pull(50, HEAD_A, state="closed", merged=True),
        51: pull(51, HEAD_B),
        52: pull(52, HEAD_C, state="closed", merged=True),
        53: pull(53, HEAD_D, state="closed", merged=True),
        54: pull(54, HEAD_A, state="closed", merged=True),
        55: pull(55, HEAD_B, state="closed", merged=True),
        56: pull(56, HEAD_C, state="closed", merged=True),
    }
    comments["50"] = [receipt_comment(5001, "2026-10-26T10:00:00Z", HEAD_A[:12]), summary_comment(5002, "2026-10-26T10:01:00Z", HEAD_A, False)]
    comments["51"] = []
    comments["52"] = [summary_comment(5201, "2026-10-26T10:01:00Z", HEAD_C, True)]
    comments["53"] = [summary_comment(5301, "2026-10-26T10:01:00Z", HEAD_D, False)]
    comments["54"] = [summary_comment(5401, "2026-10-26T10:01:00Z", HEAD_A, False)]
    comments["54"][0]["user"] = {"login": "someone"}
    comments["55"] = [receipt_comment(5501, "2026-10-26T10:02:00Z", HEAD_B, True)]
    comments["56"] = [summary_comment(5601, "2026-10-26T10:01:00Z", HEAD_B, False)]
    reactions = {50: [thumbs()], 52: [thumbs()], 53: [thumbs("someone")], 54: [thumbs()], 55: [thumbs()], 56: [thumbs()]}
    steps = [
        step("release_task", {"issue_number": number, "status": "done", "reason": "all finished"})
        for number in (40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 60, 61, 62, 63, 64)
    ]
    # Scan, then apply. Task 65 is edited by someone else while the gate reads the issue list.
    drift = {"op": "list_issues", "number": 65, "set": {"updated_at": "2026-10-29T12:30:00Z", "comments": 3}}
    steps.append(step("release_task", {"issue_number": 65, "status": "done", "reason": "all finished"}, drift=drift))
    steps.append(step("release_task", {"issue_number": 65, "status": "done", "reason": "all finished"}))
    steps.append(step("get_tasks", {"kind": "all", "state": "all", "limit": 100}))
    return story("done gate", issues, comments, steps, pulls=pulls, reactions=reactions)


def review_story():
    thread = {}
    pulls = {}
    issues = []

    def add(number, head=HEAD_A, comments=(), **kw):
        issues.append(pr_issue(number))
        pulls[number] = pull(number, head, **kw)
        thread[str(number)] = list(comments)

    add(80)
    add(81, state="closed")
    add(82, state="closed", merged=True)
    add(83, comments=[receipt_comment(8301, "2026-10-27T10:00:00Z", HEAD_A, True)])
    add(84, comments=[summary_comment(8401, "2026-10-27T10:00:00Z", HEAD_A, True)])
    add(85, comments=[receipt_comment(8501, "2026-10-27T10:00:00Z", HEAD_A, False)])
    add(86, HEAD_D, comments=[
        summary_comment(8601, "2026-10-27T09:00:00Z", HEAD_A, True),
        summary_comment(8602, "2026-10-27T10:00:00Z", HEAD_B, True),
        receipt_comment(8603, "2026-10-27T11:00:00Z", HEAD_C, True),
    ])
    add(87, comments=[comment(8701, "2026-10-27T17:00:00Z", "@codex review", login="egregore[bot]")])
    add(88, comments=[summary_comment(8801, "2026-10-27T10:00:00Z", HEAD_A, False, status="in_progress")])
    add(89, HEAD_B, comments=[
        summary_comment(8901, "2026-10-27T09:00:00Z", HEAD_A, True),
        summary_comment(8902, "2026-10-27T10:00:00Z", HEAD_B, True),
    ])
    add(90, HEAD_C, comments=[
        summary_comment(9001, "2026-10-27T09:00:00Z", HEAD_A, True),
        summary_comment(9002, "2026-10-27T10:00:00Z", HEAD_B, True),
        comment(9003, "2026-10-27T11:00:00Z", "@codex review", login="egregore[bot]"),
        summary_comment(9004, "2026-10-27T11:30:00Z", HEAD_C, False),
    ])
    add(91, comments=[comment(9101, "2026-10-27T10:00:00Z", "codex-review-state:v1 " + json.dumps({"code_review": {"status": "completed", "commit_sha": HEAD_A, "has_unresolved_findings": False}}), login="someone")])
    add(92, "not-a-sha")
    add(93, comments=[comment(9301, "2026-10-27T17:00:00Z", "@codex review", login="egregore[bot]"),
                      receipt_comment(9302, "2026-10-27T17:30:00Z", HEAD_B, False)])
    add(94, HEAD_A[:20], comments=[summary_comment(9401, "2026-10-27T10:00:00Z", HEAD_A, False)])
    pulls[95] = {"number": 95, "head": None}
    issues.append(pr_issue(95))
    thread["95"] = []
    pulls[96] = {"number": 96, "state": "OPEN", "merged": None, "head": {"sha": HEAD_C}}
    issues.append(pr_issue(96))
    thread["96"] = []
    numbers = [80, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 999, 0, -1]
    steps = [step("request_review", {"pull_number": number}, agent="codex" if number == 83 else "cursor") for number in numbers]
    steps.append(step("get_task_history", {"issue_number": 80}))
    return story("request review", issues, thread, steps, pulls=pulls)


def handoff_story():
    def held_task(number, title, body, labels):
        return task(number, title, labels=labels, body=body), held_by("cursor")

    rows = {}
    rows[100] = held_task(100, "WHOOP side", task_body("cursor", "whoop-lens"), ["type:task", "status:in-progress", "agent:cursor", "project:whoop-lens", "priority:high", "other:ignored"])
    other = task_body("cursor", "other").replace("### Role", "### Project slug\n\nx-thing\n\n### Role", 1)
    rows[101] = held_task(101, "Other project", other + "\nCode lives at https://github.com/AStrasler/Egeregore and https://github.com/someone/x-thing\n", ["type:task", "status:claimed", "agent:cursor"])
    rows[102] = held_task(102, "Weather side", task_body("cursor", "weather-intelligence"), ["type:task", "status:claimed", "agent:cursor", "project:weather-intelligence"])
    no_slug = task_body("cursor", "other")
    rows[103] = held_task(103, "Other with no slug", no_slug, ["type:task", "status:claimed", "agent:cursor"])
    rows[104] = held_task(104, "Control lines", task_body("cursor"), ["type:task", "status:claimed", "agent:cursor"])
    no_project = task_body("cursor").replace("### Project\n\ncoordination\n\n", "")
    rows[105] = held_task(105, "No project", no_project, ["type:task", "status:claimed", "agent:cursor"])
    rows[106] = held_task(106, "Access heading in text", task_body("cursor"), ["type:task", "status:claimed", "agent:cursor"])
    rows[107] = held_task(107, "Control lines in progress", task_body("cursor"), ["type:task", "status:claimed", "agent:cursor"])
    issues = [rows[n][0] for n in sorted(rows)]
    comments = {str(n): rows[n][1] for n in rows}
    arabic = "Lease-Expires: \u0662\u0660\u0662\u0666-\u0661\u0660-\u0662\u0667T\u0662\u0660:\u0660\u0660:\u0660\u0660Z"
    controls = f"first\n   Lease-Expires: 2026-10-27T23:00:00Z\r\nClaim-Agent: cursor\n```\nLease-Release: cursor  \n```\n{arabic}\nplain"
    long_title = "A very long title with accents \u00e9\u00e8\u00ea and an emoji \U0001F600 " * 4
    steps = [
        step("handoff_task", {"issue_number": 100, "to_agent": "grokbot", "done_so_far": "did the first part", "next_action": "do the second part", "title": long_title}),
        step("handoff_task", {"issue_number": 101, "to_agent": " ChatGPT ", "done_so_far": "ok", "next_action": "go"}),
        step("handoff_task", {"issue_number": 102, "to_agent": "aaron", "done_so_far": "ok", "next_action": "go", "title": "   "}),
        step("handoff_task", {"issue_number": 103, "to_agent": "codex", "done_so_far": "ok", "next_action": "go"}),
        step("handoff_task", {"issue_number": 104, "to_agent": "codex", "done_so_far": controls, "next_action": controls}),
        step("handoff_task", {"issue_number": 105, "to_agent": "grokbot", "done_so_far": "ok", "next_action": "go"}),
        step("handoff_task", {"issue_number": 106, "to_agent": "grokbot", "done_so_far": "ok\n### Access\n\nnothing here", "next_action": "go"}),
        step("post_progress", {"issue_number": 107, "done": controls, "next": controls}),
        step("renew_lease", {"issue_number": 107, "lease_hours": 3, "note": controls}),
        step("release_task", {"issue_number": 107, "status": "blocked", "reason": controls}),
        step("get_task_history", {"issue_number": 107}),
        step("get_task_history", {"issue_number": 104}),
        step("get_tasks", {"kind": "all", "state": "all", "limit": 100}),
    ]
    return story("handoffs and text", issues, comments, steps)


def failure_story():
    issues = [
        task(80, "Ready one"),
        task(81, "Held one", labels=["type:task", "status:claimed", "agent:cursor"]),
        task(82, "Held two", labels=["type:task", "status:in-progress", "agent:cursor"]),
        task(83, "Ready two", labels=["type:task", "status:ready", "agent:codex"]),
        pr_issue(84),
        task(85, "Ready three"),
        task(86, "Ready four"),
    ]
    comments = {"80": [], "81": held_by("cursor"), "82": held_by("cursor"), "83": [], "84": [], "85": [], "86": []}
    steps = [
        step("claim_task", {"issue_number": 80}, fault=fault("create_comment", 500, "boom {{ghp}} detail\nsecond line")),
        step("claim_task", {"issue_number": 85}, fault=fault("list_comments", 502, "bad gateway", skip=1)),
        step("claim_task", {"issue_number": 86}, fault=fault("add_labels", 500, "x" * 400)),
        step("claim_task", {"issue_number": 80}, fault=fault("get_issue", 403, "forbidden")),
        step("claim_task", {"issue_number": 80}),
        step("get_task", {"issue_number": 86}),
        step("renew_lease", {"issue_number": 81}, fault=fault("create_comment", 403, "Resource not accessible")),
        step("post_progress", {"issue_number": 81, "done": "d", "next": "n"}, fault=fault("get_issue", 500, "down", skip=1)),
        step("release_task", {"issue_number": 81, "status": "blocked", "reason": "r"}, fault=fault("remove_label", 500, "label failure")),
        step("release_task", {"issue_number": 81, "status": "blocked", "reason": "r"}),
        step("handoff_task", {"issue_number": 82, "to_agent": "codex", "done_so_far": "d", "next_action": "n"}, fault=fault("create_issue", 422, "Validation Failed")),
        step("handoff_task", {"issue_number": 82, "to_agent": "codex", "done_so_far": "d", "next_action": "n"}, fault=fault("create_comment", 500, "note failed")),
        step("claim_task", {"issue_number": 83}, agent="codex", fault=fault("remove_label", 404, "Label does not exist")),
        step("get_task", {"issue_number": 83}),
        step("request_review", {"pull_number": 84}, fault=fault("get_pull", 500, "pull down")),
    ]
    return story("github failures", issues, comments, steps, pulls={84: pull(84)})


STORIES = [lifecycle_story, validation_story, done_story, review_story, handoff_story, failure_story]


def _has_digit_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(key.isdigit() or _has_digit_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_digit_key(item) for item in value)
    return False


async def _record_story(item: dict) -> dict:
    live = expand(copy.deepcopy(item["dataset"]))
    store = FixtureStore(live)
    now = datetime.fromisoformat(NOW.replace("Z", "+00:00")).astimezone(timezone.utc)
    steps = []
    for entry in item["steps"]:
        service = CoordinationService(store, entry["agent"], repo=REPO, now=lambda: now, auth_mode="github_app")
        store.log = []
        store.fault = copy.deepcopy(expand(entry["fault"])) if entry.get("fault") else None
        store.drift = copy.deepcopy(entry["drift"]) if entry.get("drift") else None
        async with Client(build_server(service)) as client:
            result = await client.call_tool(entry["tool"], expand(entry["args"]))
        text = result.content[0].text
        structured = result.structured_content
        assert json.loads(text) == structured, (item["name"], entry)
        steps.append(
            {
                **entry,
                "expected": structured,
                "text": text,
                # JavaScript objects order integer-like keys first. Python keeps document order.
                "text_exact": not _has_digit_key(structured),
                "calls": store.log,
            }
        )
    store.fault = None
    return {"name": item["name"], "dataset": item["dataset"], "steps": steps}


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


async def _record_tools() -> list[dict]:
    service = CoordinationService(
        FixtureStore({"issues": [], "comments": {}}), "cursor", repo=REPO, auth_mode="github_app"
    )
    async with Client(build_server(service)) as client:
        tools = (await client.list_tools()).tools
    out = []
    for tool in sorted(tools, key=lambda item: item.name):
        schema = tool.input_schema
        note = tool.annotations
        out.append(
            {
                "name": tool.name,
                "description": tool.description,
                "properties": {name: prop.get("type") for name, prop in schema.get("properties", {}).items()},
                "required": sorted(schema.get("required", [])),
                "readOnlyHint": note.read_only_hint,
                "destructiveHint": note.destructive_hint,
                "idempotentHint": note.idempotent_hint,
                "openWorldHint": note.open_world_hint,
            }
        )
    return out


def build() -> dict:
    dataset = build_dataset()
    return {
        "note": "Generated by worker/test/parity/record.py from the Python MCP server. Do not edit.",
        "now": NOW,
        "repo": REPO,
        "tools": asyncio.run(_record_tools()),
        "dataset": dataset,
        "cases": asyncio.run(_record(dataset)),
        "stories": [asyncio.run(_record_story(make())) for make in STORIES],
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=True) + "\n"


def main() -> None:
    FIXTURES.write_text(render(build()), encoding="utf-8")
    print(f"wrote {FIXTURES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
