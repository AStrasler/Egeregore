#!/usr/bin/env python3
"""Unit tests for the Egregore MCP service. The GitHub backend is in memory."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "tests"))

import coordination as coord  # noqa: E402
from egregore_mcp import github_api  # noqa: E402
from egregore_mcp.comments import (  # noqa: E402
    REVIEW_COMMENT,
    contains_lease_control,
    handoff_issue_body,
    progress_comment,
)
from egregore_mcp.server import TOOLS, build_server, http_bind  # noqa: E402
from egregore_mcp.service import (  # noqa: E402
    MAX_REVIEW_ROUNDS,
    CoordinationService,
    completed_reviews,
    review_block,
)
from fake_github import FakeGitHub, serve  # noqa: E402

NOW = datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc)
BOUNDARY = coord.BOUNDARY_SENTENCE
HEAD = "a" * 40
TOKEN = "test-token"


class Clock:
    def __init__(self, when: datetime) -> None:
        self.when = when

    def __call__(self) -> datetime:
        return self.when


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_body(
    role: str = "any",
    *,
    project: str = "coordination",
    approval: str = "no",
    context: str = "https://github.com/AStrasler/Egeregore/issues/13",
    outcome: str = "The MCP server is on a branch.",
    out_of_scope: str = "No merge and no deploy.",
) -> str:
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
            outcome,
            "",
            "### Context",
            "",
            context,
            "",
            "### Out of scope",
            "",
            out_of_scope,
            "",
            "### Approval needed",
            "",
            approval,
            "",
            "### Data boundary",
            "",
            f"- [x] {BOUNDARY}",
            "",
        ]
    )


def decision_body() -> str:
    return "\n".join(
        [
            "### Project",
            "",
            "coordination",
            "",
            "### Decision type",
            "",
            "merge",
            "",
            "### Request",
            "",
            "Merge the MCP pull request after review.",
            "",
            "### Why Aaron",
            "",
            "Merging to the default branch is an owner action.",
            "",
            "### Data boundary",
            "",
            f"- [x] {BOUNDARY}",
            "",
        ]
    )


def service_for(store: FakeGitHub, agent: str, clock: Clock) -> CoordinationService:
    return CoordinationService(store, agent, repo=store.repo, now=clock)  # type: ignore[arg-type]


def summary_comment(sha: str, *, unresolved: bool, comment_id: int, created: datetime, updated: datetime | None = None) -> dict[str, object]:
    payload = {
        "code_review": {
            "commit_sha": sha,
            "has_any_findings": unresolved,
            "has_unresolved_findings": unresolved,
            "status": "completed",
        }
    }
    import json

    body = f"<!-- codex-review-state:v1 {json.dumps(payload)} -->\n"
    return {
        "id": comment_id,
        "body": body,
        "created_at": iso(created),
        "updated_at": iso(updated or created),
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }


def receipt_comment(sha: str, comment_id: int, created: datetime, *, passed: bool) -> dict[str, object]:
    sentence = "Didn't find any major issues. Hooray!" if passed else "Found a problem."
    body = f"<!-- codex-review-receipt:v1 -->\n\nCodex Review: {sentence}\n\n**Reviewed commit:** `{sha}`\n"
    return {
        "id": comment_id,
        "body": body,
        "created_at": iso(created),
        "updated_at": iso(created),
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock(NOW)
        self.store = FakeGitHub(now=self.clock)
        self.number = self.store.seed_issue(
            title="[Task]: Build the server",
            body=task_body(),
            labels=["type:task", "status:ready", "project:coordination", "priority:normal", "lease:expired"],
        )
        self.cursor = service_for(self.store, "cursor", self.clock)
        self.codex = service_for(self.store, "codex", self.clock)

    def test_reuses_coordination_lease_function(self) -> None:
        library = sys.modules["egregore_mcp.service"].coord
        self.assertIs(coord.assess_lease, library.assess_lease)
        self.assertIs(coord.decision_plan, library.decision_plan)
        self.assertIs(coord.stall_plan, library.stall_plan)
        self.assertIs(coord.stall_reason, library.stall_reason)

    def test_stall_view_matches_lease_watch_and_does_not_flag(self) -> None:
        idle = self.store.seed_issue(
            title="[Task]: Wait for review",
            body=task_body("cursor"),
            labels=["type:task", "status:needs-review", "project:coordination"],
        )
        created = NOW - timedelta(days=5)
        labels = self.store.label_names(idle)
        would_post, would_label = coord.stall_plan(
            labels, created, [], NOW, lease_active=False
        )
        self.assertIsNotNone(would_post)
        self.assertTrue(would_label)
        assert would_post is not None
        self.assertIn("<!-- egregore-stall:", would_post)

        viewed = self.cursor.get_task(idle)
        self.assertTrue(viewed["ok"], viewed)
        stall = viewed["result"]["issue"]["stall"]
        self.assertEqual(stall["reason"], coord.stall_reason(labels))
        self.assertEqual(stall["reason"], "needs-review")
        self.assertFalse(stall["flagged"])
        self.assertFalse(stall["lease_blocks_flag"])
        self.assertEqual(self.store.list_comments(idle), [])
        self.assertNotIn(coord.STALL_LABEL, self.store.label_names(idle))

        self.store.create_comment(idle, would_post)
        self.store.add_labels(idle, [coord.STALL_LABEL])
        history = self.cursor.get_task_history(idle)
        self.assertEqual(history["result"]["events"][0]["kind"], "stall")

        refused = self.cursor.claim_task(idle)
        self.assertEqual(refused["error"]["code"], "not_claimable")
        self.assertEqual(len(self.store.list_comments(idle)), 1)
        self.assertIn("status:needs-review", self.store.label_names(idle))
        self.assertNotIn("status:claimed", self.store.label_names(idle))

        held = self.cursor.claim_task(self.number)
        self.assertTrue(held["ok"], held)
        ready = self.cursor.get_task(self.number)
        self.assertIsNone(ready["result"]["issue"]["stall"]["reason"])
        self.assertTrue(ready["result"]["issue"]["stall"]["lease_blocks_flag"])
        lease = coord.assess_lease(self.store.list_comments(self.number), NOW)
        self.assertEqual(
            coord.stall_plan(
                self.store.label_names(self.number),
                created,
                self.store.list_comments(self.number),
                NOW,
                lease_active=lease.status == "active",
            ),
            (None, False),
        )

        handoff = self.store.seed_issue(
            title="[Handoff]: Review the server",
            body="### To\n\ncodex\n",
            labels=["type:handoff", "status:ready", "agent:codex"],
        )
        handed = self.cursor.get_task(handoff)
        self.assertEqual(handed["result"]["issue"]["stall"]["reason"], "handoff")
        self.assertFalse(handed["result"]["issue"]["stall"]["lease_blocks_flag"])

    def test_claim_sets_labels_and_the_agent_line(self) -> None:
        result = self.cursor.claim_task(self.number)
        self.assertTrue(result["ok"], result)
        lease = result["result"]["lease"]
        self.assertEqual(lease["agent"], "cursor")
        self.assertEqual(lease["status"], "active")
        self.assertEqual(self.store.label_names(self.number), ["type:task", "project:coordination", "priority:normal", "status:claimed", "agent:cursor"])
        body = self.store.list_comments(self.number)[0]["body"]
        self.assertTrue(str(body).startswith("Agent: Cursor\n"))
        self.assertIn("Claim-Agent: cursor\n", str(body))
        self.assertIn("Lease-Expires:", str(body))
        self.assertEqual(self.store.list_comments(self.number)[0]["user"]["login"], "AStrasler")

    def test_invalid_lease_hours_posts_nothing(self) -> None:
        for value in (0, 25, True):
            result = self.cursor.claim_task(self.number, value)  # type: ignore[arg-type]
            self.assertEqual(result["error"]["code"], "invalid_input")
        self.assertEqual(self.store.list_comments(self.number), [])
        self.assertIn("status:ready", self.store.label_names(self.number))

    def test_approval_yes_blocks_claim_and_holder_ops_without_the_label(self) -> None:
        title = "[Task]: Wait for Aaron"
        body = task_body("cursor", approval="yes")
        self.assertTrue(coord.validate_issue(title, body).needs_owner)
        number = self.store.seed_issue(
            title=title,
            body=body,
            labels=["type:task", "status:ready", "project:coordination"],
        )
        refused = self.cursor.claim_task(number)
        self.assertEqual(refused["error"]["code"], "not_claimable")
        self.assertEqual(self.store.list_comments(number), [])
        self.assertNotIn("status:needs-owner", self.store.label_names(number))
        self.store.create_comment(
            number,
            "Agent: Cursor\n\n## Claim\nClaim-Agent: cursor\nLease-Expires: 2026-10-28T00:00:00Z\n",
        )
        for result in (
            self.cursor.post_progress(number, "Started.", "Stop."),
            self.cursor.renew_lease(number, 2, "still waiting"),
            self.cursor.release_task(number, "ready", "stopping"),
            self.cursor.handoff_task(number, "codex", "Done.", "Review.", "Review it"),
        ):
            self.assertEqual(result["error"]["code"], "not_claimable")
        self.assertEqual(len(self.store.list_comments(number)), 1)

    def test_handoff_names_the_project_repository(self) -> None:
        self.cursor.claim_task(self.number)
        local = self.cursor.handoff_task(self.number, "codex", "Server is up.", "Review it.", "Review here")
        self.assertTrue(local["ok"], local)
        local_access = self.codex.get_task(local["result"]["handoff_issue"])["result"]["sections"]["Access"]
        self.assertIn("https://github.com/AStrasler/Egeregore", local_access)
        self.assertIn("does not grant", local_access)

        whoop = self.store.seed_issue(
            title="[Task]: Fix the week view",
            body=task_body("cursor", project="whoop-lens"),
            labels=["type:task", "status:ready", "project:whoop-lens"],
        )
        self.cursor.claim_task(whoop)
        handed = self.cursor.handoff_task(whoop, "codex", "Empty state is local.", "Open the pull request.", "Week view")
        self.assertTrue(handed["ok"], handed)
        access = self.codex.get_task(handed["result"]["handoff_issue"])["result"]["sections"]["Access"]
        self.assertIn("https://github.com/AStrasler/WHOOP-Lens", access)
        self.assertNotIn("AStrasler/Egeregore", access)
        self.assertIn("does not grant", access)

        weather = self.store.seed_issue(
            title="[Task]: Check the forecast card",
            body=task_body(
                "cursor",
                project="weather-intelligence",
                context="https://github.com/AStrasler/weather-intelligence/issues/4",
            ),
            labels=["type:task", "status:ready", "project:weather-intelligence"],
        )
        self.cursor.claim_task(weather)
        forecast = self.cursor.handoff_task(weather, "codex", "Card is empty.", "Read the issue.", "Forecast card")
        self.assertTrue(forecast["ok"], forecast)
        weather_access = self.codex.get_task(forecast["result"]["handoff_issue"])["result"]["sections"]["Access"]
        self.assertIn("https://github.com/AStrasler/weather-intelligence", weather_access)
        self.assertNotIn("AStrasler/Egeregore", weather_access)

    def test_only_ready_or_expired_tasks_are_claimable(self) -> None:
        cases = (
            ("[Evidence]: A note", ["type:evidence"], "Saw the branch."),
            ("[Review]: Check the pull request", ["type:review", "status:ready"], "### Pull request\n\n#14\n"),
            ("[Decision]: Merge the server", ["type:decision", "status:needs-owner"], decision_body()),
            ("[Task]: No status yet", ["type:task"], task_body()),
        )
        for title, labels, body in cases:
            number = self.store.seed_issue(title=title, body=body, labels=labels)
            refused = self.cursor.claim_task(number)
            self.assertEqual(refused["error"]["code"], "not_claimable", title)
            self.assertEqual(self.store.list_comments(number), [], title)
            self.assertNotIn("status:claimed", self.store.label_names(number), title)
            self.assertNotIn("agent:cursor", self.store.label_names(number), title)

        self.cursor.claim_task(self.number, 1)
        self.cursor.post_progress(self.number, "Server is up.", "Keep going.")
        self.assertIn("status:in-progress", self.store.label_names(self.number))
        self.clock.when = NOW + timedelta(hours=2)
        taken = self.codex.claim_task(self.number, 2)
        self.assertTrue(taken["ok"], taken)
        self.assertEqual(taken["result"]["lease"]["status"], "active")
        self.assertEqual(taken["result"]["lease"]["agent"], "codex")
        self.assertIn("status:claimed", self.store.label_names(self.number))

    def test_named_role_can_claim_a_ready_handoff(self) -> None:
        self.cursor.claim_task(self.number)
        handed = self.cursor.handoff_task(
            self.number,
            "codex",
            "The branch is open.",
            "Read the pull request.",
            "Review the MCP server",
        )
        self.assertTrue(handed["ok"], handed)
        number = handed["result"]["handoff_issue"]
        self.assertIn("status:ready", self.store.label_names(number))
        wrong = self.cursor.claim_task(number)
        self.assertEqual(wrong["error"]["code"], "wrong_role")
        self.assertEqual(self.store.list_comments(number), [])
        claimed = self.codex.claim_task(number)
        self.assertTrue(claimed["ok"], claimed)
        self.assertEqual(claimed["result"]["lease"]["agent"], "codex")
        self.assertIn("status:claimed", self.store.label_names(number))
        self.assertIn("agent:codex", self.store.label_names(number))

        blocked = self.store.seed_issue(
            title="[Handoff]: Waiting",
            body="### To\n\ncodex\n",
            labels=["type:handoff", "status:blocked", "agent:codex"],
        )
        self.assertEqual(self.codex.claim_task(blocked)["error"]["code"], "not_claimable")
        self.assertEqual(self.store.list_comments(blocked), [])
        closed = self.store.seed_issue(
            title="[Handoff]: Finished",
            body="### To\n\ncodex\n",
            labels=["type:handoff", "status:ready", "agent:codex"],
            state="closed",
        )
        self.assertEqual(self.codex.claim_task(closed)["error"]["code"], "not_claimable")
        self.assertEqual(self.store.list_comments(closed), [])
        self.assertEqual(self.store.issues[closed]["state"], "closed")

    def test_closed_issue_is_not_claimable(self) -> None:
        number = self.store.seed_issue(
            title="[Task]: Already finished",
            body=task_body(),
            labels=["type:task", "status:ready", "project:coordination"],
            state="closed",
        )
        refused = self.cursor.claim_task(number)
        self.assertEqual(refused["error"]["code"], "not_claimable")
        self.assertEqual(self.store.list_comments(number), [])
        self.assertEqual(self.store.label_names(number), ["type:task", "status:ready", "project:coordination"])
        self.assertEqual(self.store.issues[number]["state"], "closed")

    def test_blocked_and_needs_review_are_not_claimable(self) -> None:
        blocked = self.store.seed_issue(
            title="[Task]: Waiting on a credential",
            body=task_body("cursor"),
            labels=["type:task", "status:blocked", "project:coordination"],
        )
        refused = self.cursor.claim_task(blocked)
        self.assertEqual(refused["error"]["code"], "not_claimable")
        self.assertEqual(self.store.list_comments(blocked), [])
        self.assertEqual(self.store.label_names(blocked), ["type:task", "status:blocked", "project:coordination"])

        review = self.store.seed_issue(
            title="[Task]: Waiting on review",
            body=task_body("cursor"),
            labels=["type:task", "status:needs-review"],
        )
        waiting = self.cursor.claim_task(review)
        self.assertEqual(waiting["error"]["code"], "not_claimable")
        self.assertEqual(self.store.list_comments(review), [])
        self.assertIn("status:needs-review", self.store.label_names(review))

        self.cursor.claim_task(self.number, 1)
        self.clock.when = NOW + timedelta(hours=2)
        self.assertIn("status:claimed", self.store.label_names(self.number))
        taken = self.codex.claim_task(self.number, 2)
        self.assertTrue(taken["ok"], taken)
        self.assertEqual(taken["result"]["lease"]["agent"], "codex")
        self.assertIn("status:claimed", self.store.label_names(self.number))

    def test_wrong_role_and_owner_gate_do_not_post(self) -> None:
        owned = self.store.seed_issue(
            title="[Task]: Wait",
            body=task_body("cursor"),
            labels=["type:task", "status:needs-owner"],
        )
        refused = self.codex.claim_task(self.number and self._role_issue())
        self.assertEqual(refused["error"]["code"], "wrong_role")
        gated = self.cursor.claim_task(owned)
        self.assertEqual(gated["error"]["code"], "not_claimable")
        self.assertEqual(self.store.list_comments(owned), [])

    def _role_issue(self) -> int:
        return self.store.seed_issue(
            title="[Task]: Cursor only",
            body=task_body("cursor"),
            labels=["type:task", "status:ready"],
        )

    def test_overlap_keeps_the_earlier_claim_and_records_the_marker(self) -> None:
        first = self.cursor.claim_task(self.number, 6)
        self.assertTrue(first["ok"], first)
        lease_id = first["result"]["lease"]["lease_id"]
        labels_before = self.store.label_names(self.number)
        second = self.codex.claim_task(self.number, 4)
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"]["code"], "lease_conflict")
        self.assertEqual(second["error"]["details"]["holder"], "cursor")
        self.assertEqual(second["error"]["details"]["lease_id"], lease_id)
        self.assertIn(f"egregore-lease-conflict:{lease_id}:", second["error"]["details"]["marker"])
        self.assertEqual(self.store.label_names(self.number), labels_before)
        state = coord.assess_lease(self.store.list_comments(self.number), NOW)
        self.assertEqual(state.agent, "cursor")
        self.assertEqual(state.lease_id, lease_id)
        self.assertTrue(any("ignored overlapping claim by codex" in item for item in state.conflicts))
        conflict = self.store.list_comments(self.number)[-1]["body"]
        self.assertTrue(str(conflict).startswith("Agent: Codex\n"))
        self.assertIn(coord.lease_conflict_marker(state), str(conflict))
        self.assertNotIn("\nLease-Expires:", f"\n{conflict}")

    def test_expired_lease_can_be_taken_and_renewal_keeps_the_claim_id(self) -> None:
        claimed = self.cursor.claim_task(self.number, 1)
        self.assertTrue(claimed["ok"], claimed)
        original = claimed["result"]["lease"]["lease_id"]
        renewed = self.cursor.renew_lease(self.number, 2, "still reading")
        self.assertTrue(renewed["ok"], renewed)
        self.assertEqual(renewed["result"]["lease"]["lease_id"], original)
        self.assertEqual(renewed["result"]["lease"]["expires"], iso(NOW + timedelta(hours=2)))
        other = self.codex.renew_lease(self.number, 2)
        self.assertEqual(other["error"]["code"], "lease_not_holder")
        self.clock.when = NOW + timedelta(hours=3)
        late = self.cursor.renew_lease(self.number, 2)
        self.assertEqual(late["error"]["code"], "lease_expired")
        taken = self.codex.claim_task(self.number, 2)
        self.assertTrue(taken["ok"], taken)
        self.assertEqual(taken["result"]["lease"]["agent"], "codex")
        self.assertNotEqual(taken["result"]["lease"]["lease_id"], original)
        self.assertIn("agent:codex", self.store.label_names(self.number))
        self.assertNotIn("agent:cursor", self.store.label_names(self.number))

    def test_foreign_release_does_not_clear_the_holder(self) -> None:
        self.cursor.claim_task(self.number)
        refused = self.codex.release_task(self.number, "ready", "stepping back")
        self.assertEqual(refused["error"]["code"], "lease_not_holder")
        state = coord.assess_lease(self.store.list_comments(self.number), NOW)
        self.assertEqual(state.agent, "cursor")
        self.assertEqual(state.status, "active")

    def test_fenced_lease_directives_do_not_change_the_lease(self) -> None:
        directives = (
            "Lease-Expires: 2026-10-28T00:00:00Z",
            "Claim-Agent: cursor",
            "Lease-Release: cursor",
            "  Lease-Release: cursor  ",
        )
        for line in directives:
            self.assertTrue(contains_lease_control(f"note\n{line}\n"))
            self.assertTrue(
                coord.EXPIRES_RE.match(line.strip())
                or coord.CLAIM_AGENT_RE.match(line.strip())
                or coord.RELEASE_RE.match(line.strip())
            )
        self.assertFalse(contains_lease_control("The lease expires tomorrow."))
        claimed = self.cursor.claim_task(self.number, 6)
        self.assertTrue(claimed["ok"], claimed)
        before = coord.assess_lease(self.store.list_comments(self.number), NOW)
        fenced = "Opened the branch.\n\n```\nLease-Release: cursor\nClaim-Agent: codex\n```\n"
        posted = self.cursor.post_progress(self.number, fenced, "Lease-Expires: 2026-10-28T00:00:00Z")
        self.assertTrue(posted["ok"], posted)
        after = coord.assess_lease(self.store.list_comments(self.number), NOW)
        self.assertEqual(after.status, "active")
        self.assertEqual(after.agent, "cursor")
        self.assertEqual(after.lease_id, before.lease_id)
        self.assertEqual(after.expires, before.expires)
        progress = str(self.store.list_comments(self.number)[-1]["body"])
        self.assertIn("> Lease-Release: cursor", progress)
        for line in progress.splitlines():
            stripped = line.strip()
            if stripped.startswith(">"):
                self.assertIsNone(coord.RELEASE_RE.match(stripped))
                self.assertIsNone(coord.EXPIRES_RE.match(stripped))
                self.assertIsNone(coord.CLAIM_AGENT_RE.match(stripped))
        raw = progress_comment(agent="cursor", done="Lease-Release: cursor", next_step="next")
        self.assertIn("> Lease-Release: cursor", raw)
        self.assertNotIn("\nLease-Release:", raw)

        handed_body = handoff_issue_body(
            project="whoop-lens",
            project_slug="",
            source_agent="cursor",
            to_agent="codex",
            issue_number=self.number,
            done_so_far="```\nLease-Release: cursor\n```",
            next_action="Read the pull request.",
            repo="https://github.com/AStrasler/WHOOP-Lens",
            boundary=BOUNDARY,
        )
        self.store.create_comment(self.number, handed_body)
        still = coord.assess_lease(self.store.list_comments(self.number), NOW)
        self.assertEqual(still.status, "active")
        self.assertEqual(still.agent, "cursor")
        self.assertEqual(still.lease_id, before.lease_id)

    def test_progress_rejects_secrets_and_decisions_and_marks_in_progress(self) -> None:
        self.cursor.claim_task(self.number)
        secret = "ghp_" + ("a" * 36)
        rejected = self.cursor.post_progress(self.number, f"token {secret}", "next")
        self.assertEqual(rejected["error"]["code"], "secret_rejected")
        self.assertNotIn(secret, str(rejected))
        self.assertEqual(len(self.store.list_comments(self.number)), 1)
        forbidden = self.cursor.post_progress(self.number, "Decision: approve the merge", "next")
        self.assertEqual(forbidden["error"]["code"], "forbidden")
        posted = self.cursor.post_progress(self.number, "Opened the branch.", "Add the protocol test.")
        self.assertTrue(posted["ok"], posted)
        self.assertIn("status:in-progress", self.store.label_names(self.number))
        history = self.cursor.get_task_history(self.number)
        kinds = [event["kind"] for event in history["result"]["events"]]
        self.assertEqual(kinds, ["claim", "progress"])
        self.assertIn("Agent: Cursor", history["result"]["events"][1]["body"])

    def test_history_redacts_a_secret_that_was_already_on_the_issue(self) -> None:
        secret = "ghp_" + ("b" * 36)
        self.store.create_comment(self.number, f"please use {secret}")
        history = self.cursor.get_task_history(self.number)
        self.assertNotIn(secret, str(history))
        self.assertIn("[redacted]", history["result"]["events"][0]["body"])

    def test_handoff_opens_an_issue_and_releases_the_source(self) -> None:
        self.cursor.claim_task(self.number)
        self.cursor.post_progress(self.number, "Server is up.", "Hand the review path to Codex.")
        handed = self.cursor.handoff_task(
            self.number,
            "codex",
            "The branch is open.",
            "Read the pull request.",
            "Review the MCP server",
        )
        self.assertTrue(handed["ok"], handed)
        number = handed["result"]["handoff_issue"]
        viewed = self.codex.get_task(number)
        self.assertTrue(viewed["ok"], viewed)
        self.assertEqual(viewed["result"]["sections"]["To"], "codex")
        self.assertEqual(viewed["result"]["sections"]["From"], "cursor")
        self.assertIn("does not grant", viewed["result"]["sections"]["Access"])
        self.assertTrue(str(viewed["result"]["issue"]["title"]).startswith("[Handoff]:"))
        source = coord.assess_lease(self.store.list_comments(self.number), NOW)
        self.assertEqual(source.status, "released")
        self.assertIn("status:blocked", self.store.label_names(self.number))
        self.assertIn("agent:codex", self.store.label_names(number))
        self.assertIn("status:ready", self.store.label_names(number))

    def test_owner_decision_is_recorded_and_not_verified(self) -> None:
        number = self.store.seed_issue(
            title="[Decision]: Merge the server",
            body=decision_body(),
            labels=["type:decision", "status:needs-owner", "agent:aaron"],
        )
        self.store.create_comment(number, "Decision: approve\nAgent: Aaron\n")
        outsider = dict(self.store.create_comment(number, "Decision: approve\n"))
        self.store.comments[number][-1]["user"] = {"login": "cursor"}
        labels_before = self.store.label_names(number)
        result = self.cursor.get_owner_decisions()
        self.assertTrue(result["ok"], result)
        decisions = result["result"]["decisions"][0]["decisions"]
        self.assertEqual([item["authorization"] for item in decisions], ["recorded", "none"])
        self.assertNotIn("verified", {item["authorization"] for item in decisions})
        self.assertIn("not a verified authorization", decisions[0]["note"])
        self.assertEqual(self.store.label_names(number), labels_before)
        self.assertEqual(outsider["user"]["login"], "AStrasler")

    def test_review_request_posts_the_plain_comment_and_stops_at_three(self) -> None:
        self.store.seed_pull(8, HEAD)
        first = self.cursor.request_review(8)
        self.assertTrue(first["ok"], first)
        self.assertEqual(self.store.comments[8][0]["body"], REVIEW_COMMENT)
        self.assertNotIn("Agent:", self.store.comments[8][0]["body"])
        passed = summary_comment(HEAD, unresolved=False, comment_id=50, created=NOW, updated=NOW + timedelta(minutes=5))
        passed["updated_at"] = iso(NOW + timedelta(minutes=5))
        self.store.comments[8].append(passed)
        self.store.comments[8].append(receipt_comment(HEAD[:12], 51, NOW + timedelta(minutes=5), passed=True))
        again = self.cursor.request_review(8)
        self.assertEqual(again["error"]["code"], "review_current")
        self.assertEqual(len([item for item in self.store.comments[8] if item["body"] == REVIEW_COMMENT]), 1)

    def test_three_completed_reviews_refuse_a_fourth_head(self) -> None:
        comments = []
        for index, sha in enumerate(("b" * 40, "c" * 40, "d" * 40)):
            created = NOW + timedelta(minutes=index)
            comments.append(summary_comment(sha, unresolved=True, comment_id=index + 1, created=created))
            comments.append(receipt_comment(sha, index + 10, created, passed=False))
        rounds, pending = completed_reviews(comments)
        self.assertEqual(len(rounds), 3)
        self.assertIsNone(pending)
        blocked = review_block(comments, "e" * 40)
        assert blocked is not None
        self.assertEqual(blocked["error"]["code"], "review_limit")
        self.assertEqual(blocked["error"]["details"]["round_limit"], MAX_REVIEW_ROUNDS)

    def test_summary_findings_win_over_a_clear_receipt(self) -> None:
        full = "e" * 40
        summary = summary_comment(
            full,
            unresolved=True,
            comment_id=1,
            created=NOW,
            updated=NOW + timedelta(minutes=10),
        )
        receipt = receipt_comment(full[:10], 2, NOW + timedelta(minutes=1), passed=True)
        self.assertIn("Didn't find any major issues", str(receipt["body"]))
        for comments in ([summary, receipt], [receipt, summary]):
            rounds, pending = completed_reviews(comments)
            self.assertEqual(len(rounds), 1)
            self.assertIsNone(pending)
            self.assertTrue(rounds[0]["unresolved"])
            blocked = review_block(comments, full)
            assert blocked is not None
            self.assertEqual(blocked["error"]["code"], "review_same_head")

    def test_same_commit_receipt_and_summary_are_one_round(self) -> None:
        full = "f" * 40
        comments = [
            summary_comment(full, unresolved=False, comment_id=1, created=NOW),
            receipt_comment(full[:7], 2, NOW, passed=True),
        ]
        rounds, _pending = completed_reviews(comments)
        self.assertEqual(len(rounds), 1)
        self.assertFalse(rounds[0]["unresolved"])

    def test_a_newer_request_is_pending_until_the_summary_is_updated(self) -> None:
        summary = summary_comment("1" * 40, unresolved=False, comment_id=1, created=NOW, updated=NOW)
        request = {
            "id": 2,
            "body": "@codex review",
            "created_at": iso(NOW + timedelta(minutes=2)),
            "updated_at": iso(NOW + timedelta(minutes=2)),
            "user": {"login": "AStrasler"},
        }
        blocked = review_block([summary, request], "2" * 40)
        assert blocked is not None
        self.assertEqual(blocked["error"]["code"], "review_pending")
        summary["updated_at"] = iso(NOW + timedelta(minutes=3))
        summary["body"] = summary_comment("2" * 40, unresolved=True, comment_id=1, created=NOW)["body"]
        same = review_block([summary, request], "2" * 40)
        assert same is not None
        self.assertEqual(same["error"]["code"], "review_same_head")

    def test_get_tasks_filters_and_skips_pull_requests(self) -> None:
        self.store.seed_pull(4, HEAD, title="[Task]: not an issue")
        self.cursor.claim_task(self.number)
        listed = self.cursor.get_tasks(status="claimed", project="coordination", role="any")
        self.assertTrue(listed["ok"], listed)
        self.assertEqual([row["number"] for row in listed["result"]["tasks"]], [self.number])
        empty = self.cursor.get_tasks(kind="decision")
        self.assertEqual(empty["result"]["tasks"], [])
        bad = self.cursor.get_tasks(state="sideways")
        self.assertEqual(bad["error"]["code"], "invalid_input")

    def test_missing_configuration_and_unknown_issue(self) -> None:
        bare = CoordinationService(None, "cursor", config_problems=("EGREGORE_GITHUB_TOKEN",))
        missing = bare.get_tasks()
        self.assertEqual(missing["error"]["code"], "unconfigured")
        absent = self.cursor.get_task(999)
        self.assertEqual(absent["error"]["code"], "not_found")

    def test_tool_list_has_no_owner_actions(self) -> None:
        self.assertEqual(
            TOOLS,
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
            ],
        )
        server = build_server(self.cursor)
        names = sorted(tool.name for tool in server._tool_manager.list_tools())
        self.assertEqual(names, sorted(TOOLS))
        for banned in ("merge", "approve", "deploy", "permission", "settings"):
            self.assertFalse(any(banned in name for name in names))

    def test_http_bind_stays_on_localhost(self) -> None:
        import os

        previous = os.environ.get("EGREGORE_HTTP_HOST")
        os.environ["EGREGORE_HTTP_HOST"] = "0.0.0.0"
        try:
            with self.assertRaises(SystemExit):
                http_bind()
        finally:
            if previous is None:
                os.environ.pop("EGREGORE_HTTP_HOST", None)
            else:
                os.environ["EGREGORE_HTTP_HOST"] = previous


    def _fresh_claim(self, body: str | None = None) -> int:
        number = self.store.seed_issue(
            title="[Task]: Build the server",
            body=body or task_body(),
            labels=["type:task", "status:ready", "project:coordination"],
        )
        claimed = self.cursor.claim_task(number)
        self.assertTrue(claimed["ok"], claimed)
        return number

    def _assert_holder_writes_refused(self, number: int) -> None:
        before_comments = len(self.store.list_comments(number))
        before_labels = self.store.label_names(number)
        before_state = self.store.issues[number]["state"]
        issue_count = len(self.store.issues)
        results = (
            self.cursor.post_progress(number, "More.", "Next."),
            self.cursor.renew_lease(number, 2, "again"),
            self.cursor.handoff_task(number, "codex", "Done.", "Next.", "Next step"),
            self.cursor.release_task(number, "ready", "stopping"),
            self.cursor.release_task(number, "done", "finished"),
        )
        for result in results:
            self.assertEqual(result["error"]["code"], "not_claimable", result)
        self.assertEqual(len(self.store.list_comments(number)), before_comments)
        self.assertEqual(self.store.label_names(number), before_labels)
        self.assertEqual(self.store.issues[number]["state"], before_state)
        self.assertEqual(len(self.store.issues), issue_count)

    def test_holder_ops_refuse_each_prohibited_state_and_keep_a_live_lease(self) -> None:
        closed = self._fresh_claim()
        self.store.issues[closed]["state"] = "closed"
        self._assert_holder_writes_refused(closed)

        done = self._fresh_claim()
        self.store.issues[done]["labels"] = ["type:task", "status:done", "agent:cursor", "project:coordination"]
        self._assert_holder_writes_refused(done)

        blocked = self._fresh_claim()
        self.store.issues[blocked]["labels"] = ["type:task", "status:blocked", "agent:cursor", "project:coordination"]
        self._assert_holder_writes_refused(blocked)

        owned = self._fresh_claim()
        self.store.issues[owned]["labels"] = ["type:task", "status:needs-owner", "agent:cursor", "project:coordination"]
        self._assert_holder_writes_refused(owned)

        waiting = self.store.seed_issue(
            title="[Task]: Wait for Aaron",
            body=task_body("cursor", approval="yes"),
            labels=["type:task", "status:claimed", "agent:cursor", "project:coordination"],
        )
        self.store.create_comment(
            waiting,
            "Agent: Cursor\n\n## Claim\nClaim-Agent: cursor\nLease-Expires: 2026-10-28T00:00:00Z\n",
        )
        self._assert_holder_writes_refused(waiting)

        live = self._fresh_claim()
        progress = self.cursor.post_progress(live, "Still open.", "Continue.")
        self.assertTrue(progress["ok"], progress)
        self.assertIn("status:in-progress", self.store.label_names(live))
        renewed = self.cursor.renew_lease(live, 2, "still going")
        self.assertTrue(renewed["ok"], renewed)
        self.assertEqual(renewed["result"]["lease"]["status"], "active")
        released = self.cursor.release_task(live, "ready", "pausing")
        self.assertTrue(released["ok"], released)
        self.assertIn("status:ready", self.store.label_names(live))
        self.assertNotIn("status:done", self.store.label_names(live))

    def test_holder_ops_reread_the_issue_before_writing(self) -> None:
        ops = (
            ("progress", lambda number: self.cursor.post_progress(number, "Done.", "Next.")),
            ("renew", lambda number: self.cursor.renew_lease(number, 2, "note")),
            ("handoff", lambda number: self.cursor.handoff_task(number, "codex", "Done.", "Next.", "Title")),
            ("release", lambda number: self.cursor.release_task(number, "ready", "stopping")),
        )
        original = self.store.get_issue
        for name, op in ops:
            number = self._fresh_claim()
            calls = {"n": 0}

            def wrapped(requested: int, _number: int = number, _calls: dict[str, int] = calls) -> dict[str, object]:
                if requested == _number:
                    _calls["n"] += 1
                    if _calls["n"] == 1:
                        issue = original(requested)
                        self.store.issues[requested]["state"] = "closed"
                        return issue
                return original(requested)

            self.store.get_issue = wrapped  # type: ignore[method-assign]
            try:
                before = len(self.store.list_comments(number))
                issue_count = len(self.store.issues)
                refused = op(number)
                self.assertEqual(refused["error"]["code"], "not_claimable", name)
                self.assertGreaterEqual(calls["n"], 2, name)
                self.assertEqual(len(self.store.list_comments(number)), before, name)
                self.assertEqual(len(self.store.issues), issue_count, name)
                self.assertEqual(self.store.issues[number]["state"], "closed")
            finally:
                self.store.get_issue = original  # type: ignore[method-assign]

    def test_simultaneous_claims_keep_the_earlier_lease(self) -> None:
        import threading

        barrier = threading.Barrier(2)
        results: dict[str, dict[str, object]] = {}

        def run(name: str, service: CoordinationService) -> None:
            barrier.wait()
            results[name] = service.claim_task(self.number, 6)

        threads = (
            threading.Thread(target=run, args=("cursor", self.cursor)),
            threading.Thread(target=run, args=("codex", self.codex)),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        winners = [name for name, result in results.items() if result.get("ok")]
        self.assertEqual(len(winners), 1, results)
        state = coord.assess_lease(self.store.list_comments(self.number), NOW)
        self.assertEqual(state.status, "active")
        self.assertEqual(state.agent, winners[0])
        loser = "codex" if winners[0] == "cursor" else "cursor"
        self.assertEqual(results[loser]["error"]["code"], "lease_conflict")

    def test_status_done_requires_evidence_and_a_merged_reviewed_pull_request(self) -> None:
        docs = self._fresh_claim(
            task_body(
                outcome="The runbook paragraph is updated.",
                out_of_scope="Documentation only. No pull request.",
            )
        )
        missing = self.cursor.release_task(docs, "done", "the paragraph is in the branch")
        self.assertEqual(missing["error"]["code"], "not_done")
        self.assertEqual(len(self.store.list_comments(docs)), 1)
        self.assertIn("status:claimed", self.store.label_names(docs))
        self.store.seed_issue(
            title="[Evidence]: Runbook updated",
            body=f"### Related issue\n\n#{docs}\n\n### What happened\n\nThe paragraph is updated.\n",
            labels=["type:evidence"],
        )
        finished = self.cursor.release_task(docs, "done", "evidence is linked")
        self.assertTrue(finished["ok"], finished)
        self.assertEqual(finished["result"]["status"], "status:done")
        self.assertIn("status:done", self.store.label_names(docs))
        self.assertEqual(coord.assess_lease(self.store.list_comments(docs), NOW).status, "released")

        other = self._fresh_claim(
            task_body(
                project="other",
                outcome="A research note is filed.",
                out_of_scope="Research only.",
                context="https://github.com/AStrasler/Egeregore/issues/1",
            )
        )
        self.store.create_comment(other, "Agent: Cursor\n\n## Evidence\n\nThe note is filed.\n")
        researched = self.cursor.release_task(other, "done", "research is recorded")
        self.assertTrue(researched["ok"], researched)
        self.assertIn("status:done", self.store.label_names(other))

        unknown = self._fresh_claim(
            task_body(outcome="The widget is updated.", out_of_scope="Leave credentials alone.")
        )
        self.store.seed_issue(
            title="[Evidence]: Widget",
            body=f"### Related issue\n\n#{unknown}\n",
            labels=["type:evidence"],
        )
        unclear = self.cursor.release_task(unknown, "done", "maybe")
        self.assertEqual(unclear["error"]["code"], "not_done")
        self.assertEqual(len(self.store.list_comments(unknown)), 1)

        sha = "b" * 40
        thumbs = [{"content": "+1", "user": {"login": "chatgpt-codex-connector[bot]"}}]
        open_pull = 30
        self.store.seed_pull(open_pull, sha)
        open_task = self._fresh_claim(
            task_body(
                outcome="The server is implemented.",
                out_of_scope="Leave credentials alone.",
                context=f"https://github.com/AStrasler/Egeregore/pull/{open_pull}",
            )
        )
        self.store.seed_issue(
            title="[Evidence]: Open pull request",
            body=f"### Links\n\nhttps://github.com/AStrasler/Egeregore/issues/{open_task}\n",
            labels=["type:evidence"],
        )
        still_open = self.cursor.release_task(open_task, "done", "the pull request is open")
        self.assertEqual(still_open["error"]["code"], "not_done")
        self.assertEqual(still_open["error"]["details"]["pull_number"], open_pull)
        self.assertEqual(len(self.store.list_comments(open_task)), 1)

        closed_pull = 31
        self.store.seed_pull(closed_pull, sha, state="closed", merged=False)
        closed_task = self._fresh_claim(
            task_body(
                outcome="The server is implemented.",
                out_of_scope="Leave credentials alone.",
                context=f"https://github.com/AStrasler/Egeregore/pull/{closed_pull}",
            )
        )
        self.store.create_comment(closed_task, "## Evidence\n\nClosed without a merge.\n")
        not_merged = self.cursor.release_task(closed_task, "done", "closed")
        self.assertEqual(not_merged["error"]["code"], "not_done")
        self.assertEqual(len(self.store.list_comments(closed_task)), 2)

        merged_pull = 32
        self.store.seed_pull(merged_pull, sha, merged=True, reactions=thumbs)
        self.store.comments[merged_pull].append(
            summary_comment(sha, unresolved=True, comment_id=900, created=NOW, updated=NOW + timedelta(minutes=5))
        )
        self.store.comments[merged_pull].append(receipt_comment(sha[:12], 901, NOW + timedelta(minutes=1), passed=True))
        findings = self._fresh_claim(
            task_body(
                outcome="The server is implemented.",
                out_of_scope="Leave credentials alone.",
                context=f"https://github.com/AStrasler/Egeregore/pull/{merged_pull}",
            )
        )
        self.store.create_comment(findings, "## Evidence\n\nReview still has findings.\n")
        blocked_review = self.cursor.release_task(findings, "done", "receipt looked clear")
        self.assertEqual(blocked_review["error"]["code"], "not_done")
        self.assertNotIn("status:done", self.store.label_names(findings))
        self.assertEqual(len(self.store.list_comments(findings)), 2)

        quiet = 33
        self.store.seed_pull(quiet, sha, merged=True)
        self.store.comments[quiet].append(summary_comment(sha, unresolved=False, comment_id=902, created=NOW))
        no_thumbs = self._fresh_claim(
            task_body(
                outcome="The server is implemented.",
                out_of_scope="Leave credentials alone.",
                context=f"https://github.com/AStrasler/Egeregore/pull/{quiet}",
            )
        )
        self.store.create_comment(no_thumbs, "## Evidence\n\nSummary is clear.\n")
        missing_thumbs = self.cursor.release_task(no_thumbs, "done", "no reaction")
        self.assertEqual(missing_thumbs["error"]["code"], "not_done")
        self.assertEqual(len(self.store.list_comments(no_thumbs)), 2)

        passed = 34
        self.store.seed_pull(passed, sha, merged=True, reactions=thumbs)
        self.store.comments[passed].append(summary_comment(sha, unresolved=False, comment_id=903, created=NOW))
        implemented = self._fresh_claim(
            task_body(
                outcome="The server is implemented.",
                out_of_scope="Leave credentials alone.",
                context=f"https://github.com/AStrasler/Egeregore/pull/{passed}",
            )
        )
        self.store.create_comment(implemented, "## Evidence\n\nThe pull request merged.\n")
        marked = self.cursor.release_task(implemented, "done", "merged and reviewed")
        self.assertTrue(marked["ok"], marked)
        self.assertIn("status:done", self.store.label_names(implemented))
        self.assertEqual(coord.assess_lease(self.store.list_comments(implemented), NOW).status, "released")

        outside = self._fresh_claim(
            task_body(
                outcome="The week view is fixed.",
                out_of_scope="Leave credentials alone.",
                context="https://github.com/AStrasler/WHOOP-Lens/pull/4",
            )
        )
        self.store.create_comment(outside, "## Evidence\n\nFixed in the other repo.\n")
        elsewhere = self.cursor.release_task(outside, "done", "merged over there")
        self.assertEqual(elsewhere["error"]["code"], "not_done")
        self.assertEqual(len(self.store.list_comments(outside)), 2)
        self.assertNotIn("status:done", self.store.label_names(outside))

        missing_pull = self._fresh_claim(
            task_body(
                outcome="The server is implemented.",
                out_of_scope="Leave credentials alone.",
                context="https://github.com/AStrasler/Egeregore/pull/99",
            )
        )
        self.store.create_comment(missing_pull, "## Evidence\n\nThe link does not resolve.\n")
        missing = self.cursor.release_task(missing_pull, "done", "pull request 99")
        self.assertEqual(missing["error"]["code"], "not_found")
        self.assertEqual(len(self.store.list_comments(missing_pull)), 2)
        self.assertNotIn("status:done", self.store.label_names(missing_pull))

        gated = self.store.seed_issue(
            title="[Task]: Needs Aaron",
            body=task_body(
                approval="yes",
                outcome="The server is implemented.",
                out_of_scope="Leave credentials alone.",
                context=f"https://github.com/AStrasler/Egeregore/pull/{passed}",
            ),
            labels=["type:task", "status:claimed", "agent:cursor", "project:coordination"],
        )
        self.store.create_comment(
            gated,
            "Agent: Cursor\n\n## Claim\nClaim-Agent: cursor\nLease-Expires: 2026-10-28T00:00:00Z\n",
        )
        self.store.create_comment(gated, "## Evidence\n\nReady for Aaron.\n")
        bypass = self.cursor.release_task(gated, "done", "skip the owner")
        self.assertEqual(bypass["error"]["code"], "not_claimable")
        self.assertNotIn("status:done", self.store.label_names(gated))
        self.assertEqual(len(self.store.list_comments(gated)), 2)

    def test_status_done_refuses_a_required_pull_request_with_no_link(self) -> None:
        number = self._fresh_claim(
            task_body(
                outcome="Ship the server in a pull request.",
                context="Implementation notes for the MCP server.",
                out_of_scope="Leave credentials alone.",
            )
        )
        self.store.create_comment(number, "## Evidence\n\nThe branch is ready.\n")
        before = len(self.store.list_comments(number))
        refused = self.cursor.release_task(number, "done", "no pull request linked")
        self.assertEqual(refused["error"]["code"], "not_done")
        self.assertIn("no pull request URL or number is linked", refused["error"]["message"])
        self.assertEqual(len(self.store.list_comments(number)), before)
        self.assertIn("status:claimed", self.store.label_names(number))
        self.assertNotIn("status:done", self.store.label_names(number))
        self.assertEqual(coord.assess_lease(self.store.list_comments(number), NOW).status, "active")

    def test_status_done_accepts_a_required_task_with_a_merged_reviewed_pull_request(self) -> None:
        sha = "f" * 40
        pull = 60
        thumbs = [{"content": "+1", "user": {"login": "chatgpt-codex-connector[bot]"}}]
        self.store.seed_pull(pull, sha, merged=True, reactions=thumbs)
        self.store.comments[pull].append(summary_comment(sha, unresolved=False, comment_id=940, created=NOW))
        number = self._fresh_claim(
            task_body(
                outcome="Ship the server in a pull request.",
                context=f"https://github.com/AStrasler/Egeregore/pull/{pull}",
                out_of_scope="Leave credentials alone.",
            )
        )
        self.store.create_comment(number, "## Evidence\n\nThe pull request merged.\n")
        finished = self.cursor.release_task(number, "done", "merged and reviewed")
        self.assertTrue(finished["ok"], finished)
        self.assertEqual(finished["result"]["status"], "status:done")
        self.assertIn("status:done", self.store.label_names(number))
        self.assertEqual(coord.assess_lease(self.store.list_comments(number), NOW).status, "released")

    def test_status_done_accepts_a_docs_task_with_no_pull_request(self) -> None:
        number = self._fresh_claim(
            task_body(
                outcome="The runbook paragraph is updated.",
                context="https://github.com/AStrasler/Egeregore/issues/13",
                out_of_scope="Documentation only. No pull request.",
            )
        )
        self.store.create_comment(number, "## Evidence\n\nThe paragraph is updated.\n")
        finished = self.cursor.release_task(number, "done", "docs need no pull request")
        self.assertTrue(finished["ok"], finished)
        self.assertEqual(finished["result"]["status"], "status:done")
        self.assertIn("status:done", self.store.label_names(number))
        self.assertEqual(coord.assess_lease(self.store.list_comments(number), NOW).status, "released")

    def test_review_request_rejects_closed_pulls_and_duplicate_heads(self) -> None:
        closed = 40
        self.store.seed_pull(closed, HEAD, state="closed", merged=False)
        refused = self.cursor.request_review(closed)
        self.assertEqual(refused["error"]["code"], "not_reviewable")
        self.assertEqual(self.store.list_comments(closed), [])

        merged = 41
        self.store.seed_pull(merged, HEAD, merged=True)
        merged_refusal = self.cursor.request_review(merged)
        self.assertEqual(merged_refusal["error"]["code"], "not_reviewable")
        self.assertEqual(self.store.list_comments(merged), [])

        duplicate = 42
        self.store.seed_pull(duplicate, HEAD)
        first = self.cursor.request_review(duplicate)
        self.assertTrue(first["ok"], first)
        second = self.cursor.request_review(duplicate)
        self.assertEqual(second["error"]["code"], "review_pending")
        self.assertEqual([item["body"] for item in self.store.comments[duplicate]].count(REVIEW_COMMENT), 1)
        self.store.comments[duplicate].append(
            summary_comment(HEAD, unresolved=True, comment_id=910, created=NOW, updated=NOW + timedelta(minutes=5))
        )
        third = self.cursor.request_review(duplicate)
        self.assertEqual(third["error"]["code"], "review_same_head")
        self.assertEqual([item["body"] for item in self.store.comments[duplicate]].count(REVIEW_COMMENT), 1)

        recorded = 43
        self.store.seed_pull(recorded, HEAD)
        self.store.comments[recorded].append(receipt_comment(HEAD, 911, NOW, passed=False))
        again = self.cursor.request_review(recorded)
        self.assertEqual(again["error"]["code"], "review_duplicate")
        self.assertEqual(self.store.list_comments(recorded), [self.store.comments[recorded][0]])

        limited = 44
        self.store.seed_pull(limited, "e" * 40)
        for index, sha in enumerate(("b" * 40, "c" * 40, "d" * 40)):
            self.store.comments[limited].append(
                summary_comment(sha, unresolved=True, comment_id=920 + index, created=NOW + timedelta(minutes=index))
            )
        fourth = self.cursor.request_review(limited)
        self.assertEqual(fourth["error"]["code"], "review_limit")
        self.assertEqual(fourth["error"]["details"]["round_limit"], MAX_REVIEW_ROUNDS)
        self.assertFalse(any(item["body"] == REVIEW_COMMENT for item in self.store.comments[limited]))


class RestClientTests(unittest.TestCase):
    def test_round_trip_and_token_are_not_echoed_in_errors(self) -> None:
        clock = Clock(NOW)
        store = FakeGitHub(now=clock)
        store.seed_issue(title="[Task]: One", body=task_body(), labels=["status:ready"])
        with serve(store, TOKEN) as base:
            client = github_api.RestGitHub(api=base, token=TOKEN, repo=store.repo)
            issues = client.list_issues(state="open")
            self.assertEqual(issues[0]["number"], 1)
            comment = client.create_comment(1, "Agent: Cursor\n")
            self.assertEqual(comment["user"]["login"], "AStrasler")
            client.add_labels(1, ["status:claimed"])
            client.remove_label(1, "status:ready")
            self.assertEqual(store.label_names(1), ["status:claimed"])
            with self.assertRaises(github_api.GitHubError) as missing:
                client.get_issue(404)
            self.assertEqual(missing.exception.status, 404)
            self.assertNotIn(TOKEN, missing.exception.message)
            with self.assertRaises(github_api.GitHubError) as leaked:
                client._request("GET", "fail-with-auth")
            self.assertNotIn(TOKEN, leaked.exception.message)
            self.assertIn("[redacted]", leaked.exception.message)
            self.assertEqual(
                client._next_link('<https://example.test/next>; rel="next", <https://example.test/last>; rel="last"'),
                "https://example.test/next",
            )


if __name__ == "__main__":
    unittest.main()
