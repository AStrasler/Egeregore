#!/usr/bin/env python3
"""Tests for the Egregore coordination rules. No network."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import coordination as coord  # noqa: E402

BOUNDARY = coord.BOUNDARY_SENTENCE
NOW = datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc)


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue_body(fields: dict[str, str], *, checked: bool = True, boundary: str = "checkbox") -> str:
    lines: list[str] = []
    for heading, value in fields.items():
        lines.extend([f"### {heading}", "", value, ""])
    if boundary == "checkbox":
        mark = "x" if checked else " "
        lines.extend(["### Data boundary", "", f"- [{mark}] {BOUNDARY}", ""])
    elif boundary == "line":
        lines.extend(["Data-Boundary: coordination-only", ""])
    return "\n".join(lines)


def task_fields(**overrides: str) -> dict[str, str]:
    fields = {
        "Project": "coordination",
        "Role": "cursor",
        "Priority": "normal",
        "Outcome": "The coordination docs are in a pull request.",
        "Context": "https://github.com/AStrasler/Egeregore",
        "Out of scope": "No project data.",
        "Approval needed": "no",
    }
    fields.update(overrides)
    return fields


def comment(body: str, created: datetime, comment_id: int = 1) -> dict[str, object]:
    return {"id": comment_id, "body": body, "created_at": iso(created)}


class IssueValidationTests(unittest.TestCase):
    def test_valid_task(self) -> None:
        check = coord.validate_issue("[Task]: Write the docs", issue_body(task_fields()))
        self.assertTrue(check.ok)
        self.assertEqual(check.kind, "task")
        self.assertFalse(check.needs_owner)
        self.assertFalse(check.secret)
        self.assertEqual(check.problems, ())

    def test_approval_yes_needs_owner_without_failing_the_form(self) -> None:
        check = coord.validate_issue(
            "[Task]: Merge the docs",
            issue_body(task_fields(**{"Approval needed": "yes"})),
        )
        self.assertTrue(check.ok)
        self.assertTrue(check.needs_owner)

    def test_missing_and_unchecked_boundary(self) -> None:
        missing = coord.validate_issue("[Task]: Docs", issue_body(task_fields(), boundary="absent"))
        unchecked = coord.validate_issue("[Task]: Docs", issue_body(task_fields(), checked=False))
        self.assertIn("Data boundary confirmation is missing.", missing.problems)
        self.assertIn("Data boundary checkbox is unchecked.", unchecked.problems)

    def test_boundary_line_is_accepted(self) -> None:
        check = coord.validate_issue("[Task]: Docs", issue_body(task_fields(), boundary="line"))
        self.assertTrue(check.ok)

    def test_other_project_requires_slug(self) -> None:
        missing = coord.validate_issue("[Task]: Docs", issue_body(task_fields(Project="other")))
        self.assertFalse(missing.ok)
        self.assertIn("Project slug is required when Project is other.", missing.problems)
        present = coord.validate_issue(
            "[Task]: Docs",
            issue_body(task_fields(Project="other", **{"Project slug": "whoop-lens"})),
        )
        self.assertTrue(present.ok)

    def test_secret_is_flagged_and_not_copied_into_the_comment(self) -> None:
        token = "ghp_" + ("a" * 36)
        check = coord.validate_issue(
            "[Task]: Docs",
            issue_body(task_fields(Outcome=f"use {token}")),
        )
        comment_body = coord.render_hygiene_comment(check)
        self.assertTrue(check.secret)
        self.assertTrue(check.needs_owner)
        self.assertFalse(check.ok)
        self.assertNotIn(token, comment_body)
        self.assertNotIn("ghp_", comment_body)

    def test_bad_role_and_priority_are_rejected(self) -> None:
        bad_role = coord.validate_issue("[Task]: Docs", issue_body(task_fields(Role="intern")))
        bad_priority = coord.validate_issue("[Task]: Docs", issue_body(task_fields(Priority="asap")))
        self.assertIn("Role must be chatgpt, grokbot, cursor, codex, aaron, or any.", bad_role.problems)
        self.assertIn("Priority must be low, normal, high, or urgent.", bad_priority.problems)

    def test_unknown_title_is_rejected(self) -> None:
        check = coord.validate_issue("Docs", issue_body(task_fields()))
        self.assertFalse(check.ok)
        self.assertIsNone(check.kind)

    def test_handoff_must_say_it_does_not_grant_access(self) -> None:
        fields = {
            "Project": "whoop-lens",
            "From": "grokbot",
            "To": "cursor",
            "Related issue": "#4",
            "Done so far": "Scoped the empty state.",
            "Next action": "Open a pull request in WHOOP-Lens.",
            "Access": "Cursor can just look around.",
        }
        rejected = coord.validate_issue("[Handoff]: Empty state", issue_body(fields))
        self.assertIn("Access must say that the handoff does not grant access.", rejected.problems)
        fields["Access"] = "This handoff does not grant repository access."
        accepted = coord.validate_issue("[Handoff]: Empty state", issue_body(fields))
        self.assertTrue(accepted.ok)

    def test_decision_issue_needs_owner(self) -> None:
        fields = {
            "Project": "weather-intelligence",
            "Decision type": "credentials",
            "Request": "Allow Codex to see the repo.",
            "Why Aaron": "It changes access.",
        }
        check = coord.validate_issue("[Decision]: Repo access", issue_body(fields))
        self.assertTrue(check.ok)
        self.assertTrue(check.needs_owner)

    def test_placeholder_counts_as_empty(self) -> None:
        check = coord.validate_issue("[Task]: Docs", issue_body(task_fields(Outcome="_No response_")))
        self.assertIn("Outcome is empty.", check.problems)


class LeaseTests(unittest.TestCase):
    def claim(self, agent: str, created: datetime, expires: datetime, comment_id: int = 1) -> dict[str, object]:
        body = f"Claim-Agent: {agent}\nLease-Expires: {iso(expires)}\n"
        return comment(body, created, comment_id)

    def test_active_expired_and_exact_max_lease(self) -> None:
        created = NOW - timedelta(hours=1)
        active = coord.assess_lease([self.claim("cursor", created, NOW + timedelta(hours=5))], NOW)
        self.assertEqual(active.status, "active")
        self.assertEqual(active.agent, "cursor")

        expired = coord.assess_lease([self.claim("cursor", created, NOW)], NOW)
        self.assertEqual(expired.status, "expired")

        exact = coord.assess_lease(
            [self.claim("Grokbot", created, created + timedelta(hours=24))],
            created + timedelta(hours=1),
        )
        self.assertEqual(exact.status, "active")
        self.assertEqual(exact.agent, "grokbot")

        too_long = coord.assess_lease(
            [self.claim("cursor", created, created + timedelta(hours=24, seconds=1))],
            created + timedelta(hours=1),
        )
        self.assertEqual(too_long.status, "too_long")

    def test_earliest_overlapping_claim_holds_and_release_allows_recovery(self) -> None:
        created = NOW - timedelta(hours=2)
        first = self.claim("chatgpt", created, NOW + timedelta(hours=4), 1)
        second = self.claim("cursor", created + timedelta(minutes=5), NOW + timedelta(hours=3), 2)
        overlap = coord.assess_lease([first, second], NOW)
        self.assertEqual(overlap.status, "active")
        self.assertEqual(overlap.agent, "chatgpt")
        self.assertTrue(any("ignored overlapping claim by cursor" in item for item in overlap.conflicts))

        foreign_release = comment("Lease-Release: cursor\n", NOW - timedelta(minutes=30), 3)
        still_held = coord.assess_lease([first, second, foreign_release], NOW)
        self.assertEqual(still_held.agent, "chatgpt")
        self.assertEqual(still_held.status, "active")

        holder_release = comment("Lease-Release: chatgpt\n", NOW - timedelta(minutes=20), 4)
        released = coord.assess_lease([first, holder_release], NOW)
        self.assertEqual(released.status, "released")

        recovered = self.claim("cursor", NOW - timedelta(minutes=10), NOW + timedelta(hours=2), 5)
        taken = coord.assess_lease([first, holder_release, recovered], NOW)
        self.assertEqual(taken.status, "active")
        self.assertEqual(taken.agent, "cursor")
        self.assertEqual(taken.conflicts, ())

    def test_expired_holder_can_be_recovered_by_a_later_claim(self) -> None:
        started = NOW - timedelta(hours=10)
        expired_claim = self.claim("grokbot", started, started + timedelta(hours=6), 1)
        self.assertEqual(coord.assess_lease([expired_claim], NOW).status, "expired")
        recovery = self.claim("cursor", NOW - timedelta(minutes=5), NOW + timedelta(hours=2), 2)
        state = coord.assess_lease([expired_claim, recovery], NOW)
        self.assertEqual(state.status, "active")
        self.assertEqual(state.agent, "cursor")
        self.assertEqual(state.conflicts, ())

    def test_conflict_comment_does_not_steal_or_release_the_lease(self) -> None:
        created = NOW - timedelta(hours=1)
        first = self.claim("chatgpt", created, NOW + timedelta(hours=4), 1)
        second = self.claim("codex", created + timedelta(minutes=10), NOW + timedelta(hours=1), 2)
        state = coord.assess_lease([first, second], NOW)
        follow_up = comment(coord.lease_conflict_comment(state), NOW, 3)
        again = coord.assess_lease([first, second, follow_up], NOW)
        self.assertEqual(again.status, "active")
        self.assertEqual(again.agent, "chatgpt")
        self.assertNotRegex(coord.lease_conflict_comment(state), r"(?m)^Lease-Expires:")
        self.assertNotRegex(coord.lease_conflict_comment(state), r"(?m)^Lease-Release:")
        self.assertNotRegex(coord.lease_conflict_comment(state), r"(?m)^Claim-Agent:")

    def test_renewal_keeps_the_earlier_agent(self) -> None:
        created = NOW - timedelta(hours=3)
        claim = self.claim("codex", created, created + timedelta(hours=6), 1)
        renewal = comment(f"Lease-Expires: {iso(NOW + timedelta(hours=2))}\n", NOW - timedelta(minutes=10), 2)
        state = coord.assess_lease([claim, renewal], NOW)
        self.assertEqual(state.status, "active")
        self.assertEqual(state.agent, "codex")
        self.assertEqual(state.expires, NOW + timedelta(hours=2))

    def test_agentless_renewal_after_rejected_overlap_extends_the_holder(self) -> None:
        created = NOW - timedelta(hours=2)
        holder = self.claim("cursor", created, NOW + timedelta(hours=4), 1)
        overlap = self.claim("codex", created + timedelta(minutes=5), NOW + timedelta(hours=3), 2)
        renewal_at = NOW + timedelta(hours=6)
        renewal = comment(f"Lease-Expires: {iso(renewal_at)}\n", NOW - timedelta(minutes=10), 3)
        state = coord.assess_lease([holder, overlap, renewal], NOW)
        self.assertEqual(state.status, "active")
        self.assertEqual(state.agent, "cursor")
        self.assertEqual(state.expires, renewal_at)
        self.assertEqual(
            state.conflicts,
            ("ignored overlapping claim by codex while cursor holds the lease",),
        )
        before_renewal = coord.assess_lease([holder, overlap], NOW)
        self.assertEqual(coord.lease_conflict_marker(state), coord.lease_conflict_marker(before_renewal))
        self.assertNotEqual(state.expires, before_renewal.expires)

    def test_new_holder_does_not_inherit_a_rejected_overlap(self) -> None:
        created = NOW - timedelta(hours=2)
        cursor_claim = self.claim("cursor", created, NOW + timedelta(hours=4), 1)
        rejected = self.claim("codex", created + timedelta(minutes=5), NOW + timedelta(hours=3), 2)
        held = coord.assess_lease([cursor_claim, rejected], NOW)
        self.assertEqual(held.agent, "cursor")
        self.assertEqual(
            held.conflicts,
            ("ignored overlapping claim by codex while cursor holds the lease",),
        )

        release = comment("Lease-Release: cursor\n", NOW - timedelta(minutes=40), 3)
        codex_claim = self.claim("codex", NOW - timedelta(minutes=30), NOW + timedelta(hours=2), 4)
        taken = coord.assess_lease([cursor_claim, rejected, release, codex_claim], NOW)
        self.assertEqual(taken.status, "active")
        self.assertEqual(taken.agent, "codex")
        self.assertEqual(taken.conflicts, ())
        self.assertEqual(coord.current_lease_conflicts(taken.conflicts), ())

        renewal_at = NOW + timedelta(hours=5)
        renewal = comment(f"Lease-Expires: {iso(renewal_at)}\n", NOW - timedelta(minutes=10), 5)
        renewed = coord.assess_lease([cursor_claim, rejected, release, codex_claim, renewal], NOW)
        self.assertEqual(renewed.status, "active")
        self.assertEqual(renewed.agent, "codex")
        self.assertEqual(renewed.expires, renewal_at)
        self.assertEqual(renewed.conflicts, ())
        self.assertEqual(coord.current_lease_conflicts(renewed.conflicts), ())
        self.assertEqual(coord.lease_conflict_marker(taken), coord.lease_conflict_marker(renewed))
        self.assertNotEqual(taken.expires, renewed.expires)
        self.assertEqual(taken.lease_id, "4")
        self.assertEqual(renewed.lease_id, taken.lease_id)

    def test_renewals_within_one_lease_do_not_rewarn(self) -> None:
        created = NOW - timedelta(hours=2)
        claim = self.claim("cursor", created, NOW + timedelta(hours=1), 21)
        overlap = self.claim("codex", created + timedelta(minutes=4), NOW + timedelta(minutes=30), 22)
        first = coord.assess_lease([claim, overlap], NOW)
        self.assertEqual(first.lease_id, "21")
        self.assertTrue(coord.conflict_comment_needed([claim, overlap], first))

        warned = comment(coord.lease_conflict_comment(first), NOW - timedelta(minutes=50), 23)
        first_expiry = NOW + timedelta(hours=3)
        renewal = comment(f"Lease-Expires: {iso(first_expiry)}\n", NOW - timedelta(minutes=40), 24)
        once = [claim, overlap, warned, renewal]
        extended = coord.assess_lease(once, NOW)
        self.assertEqual(extended.lease_id, "21")
        self.assertEqual(extended.expires, first_expiry)
        self.assertEqual(coord.lease_conflict_marker(extended), coord.lease_conflict_marker(first))
        self.assertFalse(coord.conflict_comment_needed(once, extended))

        second_expiry = NOW + timedelta(hours=5)
        again = comment(f"Lease-Expires: {iso(second_expiry)}\n", NOW - timedelta(minutes=20), 25)
        twice = once + [again]
        renewed = coord.assess_lease(twice, NOW)
        self.assertEqual(renewed.lease_id, "21")
        self.assertEqual(renewed.agent, "cursor")
        self.assertEqual(renewed.expires, second_expiry)
        self.assertEqual(coord.lease_conflict_marker(renewed), coord.lease_conflict_marker(first))
        self.assertFalse(coord.conflict_comment_needed(twice, renewed))

    def test_new_lease_overlap_is_not_suppressed_by_an_older_warning(self) -> None:
        created = NOW - timedelta(hours=3)
        claim = self.claim("cursor", created, NOW + timedelta(hours=4), 11)
        overlap = self.claim("codex", created + timedelta(minutes=5), NOW + timedelta(hours=1), 12)
        first = coord.assess_lease([claim, overlap], NOW)
        warned = comment(coord.lease_conflict_comment(first), NOW - timedelta(minutes=40), 13)
        release = comment("Lease-Release: cursor\n", NOW - timedelta(minutes=25), 14)
        reclaim = self.claim("cursor", NOW - timedelta(minutes=20), NOW + timedelta(hours=3), 15)
        quiet_comments = [claim, overlap, warned, release, reclaim]
        quiet = coord.assess_lease(quiet_comments, NOW)
        self.assertEqual(quiet.lease_id, "15")
        self.assertEqual(quiet.conflicts, ())
        self.assertFalse(coord.conflict_comment_needed(quiet_comments, quiet))

        again = self.claim("codex", NOW - timedelta(minutes=10), NOW + timedelta(hours=1), 16)
        comments = quiet_comments + [again]
        state = coord.assess_lease(comments, NOW)
        self.assertEqual(state.status, "active")
        self.assertEqual(state.agent, "cursor")
        self.assertEqual(state.lease_id, "15")
        self.assertNotEqual(state.lease_id, first.lease_id)
        self.assertTrue(any("overlapping claim by codex" in item for item in state.conflicts))
        self.assertTrue(coord.conflict_comment_needed(comments, state))
        self.assertNotEqual(coord.lease_conflict_marker(state), coord.lease_conflict_marker(first))
        self.assertNotIn(coord.lease_conflict_marker(state), warned["body"])

        earlier = NOW - timedelta(hours=3)
        old_claim = self.claim("cursor", earlier - timedelta(hours=2), earlier + timedelta(hours=2), 41)
        old_overlap = self.claim(
            "codex",
            earlier - timedelta(hours=2) + timedelta(minutes=5),
            earlier + timedelta(hours=1),
            42,
        )
        active = coord.assess_lease([old_claim, old_overlap], earlier)
        self.assertEqual(active.status, "active")
        self.assertEqual(active.lease_id, "41")
        old_warning = comment(coord.lease_conflict_comment(active), earlier + timedelta(minutes=10), 43)
        expired = coord.assess_lease([old_claim, old_overlap, old_warning], NOW)
        self.assertEqual(expired.status, "expired")
        self.assertEqual(expired.lease_id, "41")
        new_claim = self.claim("cursor", NOW - timedelta(minutes=30), NOW + timedelta(hours=3), 44)
        new_overlap = self.claim("codex", NOW - timedelta(minutes=20), NOW + timedelta(hours=1), 45)
        revived_comments = [old_claim, old_overlap, old_warning, new_claim, new_overlap]
        revived = coord.assess_lease(revived_comments, NOW)
        self.assertEqual(revived.status, "active")
        self.assertEqual(revived.agent, "cursor")
        self.assertEqual(revived.lease_id, "44")
        self.assertTrue(coord.conflict_comment_needed(revived_comments, revived))
        self.assertNotIn(coord.lease_conflict_marker(revived), old_warning["body"])

    def test_overlong_overlap_does_not_take_a_live_lease(self) -> None:
        created = NOW - timedelta(hours=1)
        holder = self.claim("cursor", created, NOW + timedelta(hours=4), 1)
        other = self.claim(
            "codex",
            created + timedelta(minutes=5),
            created + timedelta(minutes=5) + timedelta(hours=25),
            2,
        )
        state = coord.assess_lease([holder, other], NOW)
        self.assertEqual(state.status, "active")
        self.assertEqual(state.agent, "cursor")
        self.assertEqual(state.expires, NOW + timedelta(hours=4))
        self.assertEqual(state.lease_id, "1")
        self.assertIn("ignored overlapping claim by codex while cursor holds the lease", state.conflicts)
        self.assertTrue(coord.conflict_comment_needed([holder, other], state))

        renewal_at = NOW - timedelta(minutes=10)
        too_long = comment(
            f"Lease-Expires: {iso(renewal_at + timedelta(hours=24, seconds=1))}\n",
            renewal_at,
            3,
        )
        kept = coord.assess_lease([holder, too_long], NOW)
        self.assertEqual(kept.agent, "cursor")
        self.assertEqual(kept.expires, NOW + timedelta(hours=4))
        self.assertEqual(kept.lease_id, "1")
        self.assertTrue(any("longer than 24 hours" in item for item in kept.conflicts))

    def test_later_lease_with_the_same_expiry_is_a_separate_backstop(self) -> None:
        expiry = NOW
        first = self.claim("cursor", NOW - timedelta(hours=5), expiry, 51)
        release = comment("Lease-Release: cursor\n", NOW - timedelta(hours=3), 52)
        second = self.claim("cursor", NOW - timedelta(hours=2), expiry, 53)
        only = coord.assess_lease([first], NOW)
        both = coord.assess_lease([first, release, second], NOW)
        self.assertEqual(only.status, "expired")
        self.assertEqual(both.status, "expired")
        self.assertEqual(only.lease_id, "51")
        self.assertEqual(both.lease_id, "53")
        self.assertNotEqual(only.marker, both.marker)
        old_note = coord.lease_comment(only)
        self.assertIn(f"<!-- egregore-lease:{only.marker} -->", old_note)
        self.assertNotIn(f"<!-- egregore-lease:{both.marker} -->", old_note)

    def test_invalid_calendar_timestamp_does_not_abort_the_rest(self) -> None:
        created = NOW - timedelta(hours=1)
        bad_expiry = comment(
            "Claim-Agent: cursor\nLease-Expires: 2026-13-27T17:00:00Z\n",
            created,
            1,
        )
        bad_created = {
            "id": 2,
            "body": f"Claim-Agent: chatgpt\nLease-Expires: {iso(NOW + timedelta(hours=3))}\n",
            "created_at": "2026-13-01T00:00:00Z",
        }
        good = self.claim("grokbot", NOW - timedelta(minutes=5), NOW + timedelta(hours=2), 3)
        state = coord.assess_lease([bad_expiry, bad_created, good], NOW)
        self.assertEqual(state.status, "active")
        self.assertEqual(state.agent, "grokbot")
        self.assertTrue(any("invalid timestamp" in item for item in state.conflicts))
        only_bad = coord.assess_lease([bad_expiry], NOW)
        self.assertEqual(only_bad.status, "invalid_agent")

    def test_missing_and_unknown_agent(self) -> None:
        self.assertEqual(coord.assess_lease([], NOW).status, "missing")
        created = NOW - timedelta(hours=1)
        unknown = comment(f"Claim-Agent: someone\nLease-Expires: {iso(NOW + timedelta(hours=1))}\n", created, 8)
        self.assertEqual(coord.assess_lease([unknown], NOW).status, "invalid_agent")

    def test_backstop_comment_does_not_become_a_new_lease(self) -> None:
        created = NOW - timedelta(hours=8)
        original = self.claim("cursor", created, created + timedelta(hours=6), 1)
        state = coord.assess_lease([original], NOW)
        follow_up = comment(coord.lease_comment(state), NOW, 2)
        again = coord.assess_lease([original, follow_up], NOW)
        self.assertEqual(again.status, "expired")
        self.assertNotRegex(coord.lease_comment(state), r"(?m)^Lease-Expires:")
        self.assertNotRegex(coord.lease_comment(state), r"(?m)^Lease-Release:")

    def test_label_plan_releases_claims_and_keeps_owner_gate(self) -> None:
        expired = coord.LeaseState("expired", "cursor", NOW, "expired:stamp")
        self.assertIsNone(coord.lease_label_plan(["status:ready"], expired))
        self.assertIsNone(
            coord.lease_label_plan(
                ["status:claimed"],
                coord.LeaseState("active", "cursor", NOW + timedelta(hours=1), "active:stamp"),
            )
        )

        ready = coord.lease_label_plan(["status:in-progress", "agent:cursor"], expired)
        self.assertIsNotNone(ready)
        assert ready is not None
        self.assertIn("status:ready", ready.add)
        self.assertIn("lease:expired", ready.add)
        self.assertIn("status:in-progress", ready.remove)

        gated = coord.lease_label_plan(["status:claimed", "status:needs-owner"], expired)
        assert gated is not None
        self.assertNotIn("status:ready", gated.add)
        self.assertIn("status:claimed", gated.remove)
        self.assertIn("lease:expired", gated.add)

        finished = coord.lease_label_plan(["status:done", "status:claimed"], expired)
        assert finished is not None
        self.assertEqual(finished.add, ())
        self.assertEqual(finished.remove, ("status:claimed",))


class DecisionTests(unittest.TestCase):
    def test_login_records_a_decision_and_does_not_authorize(self) -> None:
        self.assertIsNone(coord.decision_verdict("hello\n"))
        self.assertEqual(coord.decision_verdict("\nDecision: Approve the merge\n"), "approve")
        self.assertIsNone(coord.decision_plan("AStrasler", None, "[Task]: x", []))

        for author in ("cursor", "github-actions[bot]", "", "astraler"):
            warning = coord.decision_plan(author, "approve", "[Task]: Ship it", ["status:needs-owner"])
            assert warning is not None
            self.assertEqual(warning.authorization, "none")
            self.assertEqual(warning.add, ())
            self.assertEqual(warning.remove, ())
            warning_comment = coord.render_decision_comment(warning, "9", "approve")
            self.assertNotIn("Lease-Release:", warning_comment)
            self.assertIn("Authorization: none", warning_comment)
            self.assertIn("not a verified authorization", warning.note.lower())

        for verdict, title, labels in (
            ("approve", "[Task]: Ship it", ["status:needs-owner"]),
            ("reject", "[Task]: Ship it", ["status:needs-owner", "status:claimed"]),
            ("approve", "[Decision]: Access", ["status:needs-owner", "type:decision"]),
        ):
            recorded = coord.decision_plan("AStrasler", verdict, title, labels)
            assert recorded is not None
            self.assertEqual(recorded.authorization, "recorded")
            self.assertEqual(recorded.add, ())
            self.assertEqual(recorded.remove, ())
            recorded_comment = coord.render_decision_comment(recorded, "9", verdict)
            self.assertIn("Authorization: recorded", recorded_comment)
            self.assertIn("not a verified authorization", recorded_comment)
            self.assertNotIn("Lease-Release:", recorded_comment)
            self.assertNotIn("status:ready", recorded_comment)

    def test_recorded_decision_does_not_release_a_live_lease(self) -> None:
        created = NOW - timedelta(hours=1)
        claim = comment(
            f"Claim-Agent: cursor\nLease-Expires: {iso(NOW + timedelta(hours=4))}\n",
            created,
            1,
        )
        plan = coord.decision_plan("AStrasler", "approve", "[Task]: Continue", ["status:needs-owner"])
        assert plan is not None
        note = comment(coord.render_decision_comment(plan, "4", "approve"), NOW, 4)
        state = coord.assess_lease([claim, note], NOW)
        self.assertEqual(state.status, "active")
        self.assertEqual(state.agent, "cursor")


class CliTests(unittest.TestCase):
    def test_paginated_json_arrays_are_flattened(self) -> None:
        first = json.dumps([{"id": 1}, {"id": 2}])
        second = json.dumps([{"id": 3}])
        parsed = coord.parse_gh_payload(first + "\n" + second)
        self.assertEqual([item["id"] for item in parsed], [1, 2, 3])
        self.assertEqual(coord.parse_gh_payload('{"id": 7}')["id"], 7)
        self.assertEqual(coord.parse_gh_payload('[{"id": 8}]'), [{"id": 8}])
        with self.assertRaises(json.JSONDecodeError):
            coord.parse_gh_payload(first + "\n" + '{"id": 9}')

    def test_check_issue_cli_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue.md"
            path.write_text(issue_body(task_fields()), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "coordination.py"),
                    "check-issue",
                    "--title",
                    "[Task]: Docs",
                    "--body-file",
                    str(path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])

    def test_library_has_no_shell_invocation(self) -> None:
        source = (ROOT / "scripts" / "coordination.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)


class LabelScriptTests(unittest.TestCase):
    def test_dry_run_lists_required_labels_without_gh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PATH"] = tmp
            completed = subprocess.run(
                ["/bin/bash", str(ROOT / "scripts" / "create-labels.sh"), "--dry-run"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        names = set(completed.stdout.split())
        for label in coord.STATUS_LABELS:
            self.assertIn(label, names)
        for label in (
            "lease:expired",
            "agent:chatgpt",
            "agent:grokbot",
            "agent:cursor",
            "agent:codex",
            "agent:aaron",
            "project:coordination",
            "project:whoop-lens",
            "project:weather-intelligence",
            "priority:urgent",
            "type:decision",
        ):
            self.assertIn(label, names)

    def test_extra_project_and_rejected_slug(self) -> None:
        added = subprocess.run(
            [
                "/bin/bash",
                str(ROOT / "scripts" / "create-labels.sh"),
                "--dry-run",
                "project:sample-app",
                "Coordination only. No project data in this repo.",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("project:sample-app", added.stdout.split())

        rejected = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts" / "create-labels.sh"), "--dry-run", "project:Bad", "desc"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 2)

    def test_script_refuses_other_repositories_and_keeps_short_descriptions(self) -> None:
        script = (ROOT / "scripts" / "create-labels.sh").read_text(encoding="utf-8")
        self.assertIn("Refusing to edit labels", script)
        self.assertIn('expected="AStrasler/Egeregore"', script)
        descriptions = re.findall(r'^label "[^"]+" "[^"]+" "([^"]+)"', script, re.MULTILINE)
        self.assertGreaterEqual(len(descriptions), 20)
        for description in descriptions:
            self.assertLessEqual(len(description), 100)


class RepoPolicyTests(unittest.TestCase):
    def test_codeowners_covers_github_and_tests(self) -> None:
        text = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
        self.assertIn("* @AStrasler", text)
        self.assertIn("/.github/ @AStrasler", text)
        self.assertIn("/tests/ @AStrasler", text)
        self.assertIn("/scripts/ @AStrasler", text)

    def test_issue_forms_require_the_public_data_boundary(self) -> None:
        template_dir = ROOT / ".github" / "ISSUE_TEMPLATE"
        forms = sorted(path for path in template_dir.glob("*.yml") if path.name != "config.yml")
        self.assertEqual(
            [path.name for path in forms],
            ["evidence.yml", "handoff.yml", "owner-decision.yml", "review-request.yml", "task.yml"],
        )
        config = (template_dir / "config.yml").read_text(encoding="utf-8")
        self.assertIn("blank_issues_enabled: false", config)
        for path in forms:
            text = path.read_text(encoding="utf-8")
            self.assertIn("name:", text)
            self.assertIn("description:", text)
            self.assertIn("body:", text)
            self.assertIn(BOUNDARY, text)
            self.assertIn("required: true", text)

    def test_workflows_stay_small_and_least_privilege(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        files = sorted(workflow_dir.glob("*.yml"))
        self.assertEqual(
            [path.name for path in files],
            ["ci.yml", "issue-hygiene.yml", "lease-watch.yml"],
        )
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("pull_request_target", text)
            self.assertNotIn("workflow_run", text)
            self.assertNotIn("secrets.", text)
            self.assertIn("timeout-minutes: 5", text)
            self.assertIn("runs-on: ubuntu-latest", text)
            self.assertNotIn("self-hosted", text)
            self.assertNotIn("ubuntu-latest-", text)
            self.assertNotRegex(text, r"macos-|windows-")
            self.assertNotIn("visibility", text)
            uses = re.findall(r"uses:\s*(\S+)", text)
            self.assertTrue(uses)
            for use in uses:
                self.assertEqual(use, "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1")
            permissions = permission_lines(text)
            self.assertIn("contents: read", permissions)
            if path.name == "ci.yml":
                self.assertEqual(permissions, ["contents: read"])
            else:
                self.assertEqual(permissions, ["contents: read", "issues: write"])

        hygiene = (workflow_dir / "issue-hygiene.yml").read_text(encoding="utf-8")
        self.assertIn("startsWith(github.event.comment.body, 'Decision:')", hygiene)
        ci = (workflow_dir / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python3 -m unittest discover -s tests -v", ci)
        self.assertLess(ci.index("permissions:"), ci.index("jobs:"))
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertLess(text.index("permissions:"), text.index("jobs:"))
            self.assertNotIn("contents: write", text)
            self.assertNotIn("pull-requests:", text)
            self.assertEqual(len(re.findall(r"(?m)^permissions:[ \t]*$", text)), 1)

    def test_readme_and_apache_license_are_present(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("Copyright 2026 Aaron Strasler\n"))
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        self.assertIn("APPENDIX: How to apply the Apache License to your work.", license_text)
        self.assertIn("Apache License, Version 2.0", readme)
        self.assertIn("](LICENSE)", readme)

    def test_docs_state_the_public_boundary_and_private_switch(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        operations = (ROOT / "OPERATIONS.md").read_text(encoding="utf-8")
        onboarding = (ROOT / "ONBOARDING.md").read_text(encoding="utf-8")
        for text in (readme, security, operations, onboarding):
            self.assertIn("This repository is public.", text)
            self.assertIn("health data", text)
            self.assertIn("credentials", text)
        self.assertIn("## Switching this repository to private", security)
        self.assertIn("3,000", security)
        self.assertIn("larger runners", security)
        self.assertIn("SECURITY.md#switching-this-repository-to-private", operations)
        self.assertIn("There is no MCP server", readme)
        self.assertIn("WHOOP-Lens", onboarding)
        self.assertIn("does not grant", onboarding)


def permission_lines(text: str) -> list[str]:
    collected: list[str] = []
    in_block = False
    for line in text.splitlines():
        if re.match(r"^permissions:\s*$", line):
            in_block = True
            continue
        if in_block:
            if re.match(r"^\S", line):
                break
            stripped = line.strip()
            if stripped:
                collected.append(stripped)
    return collected


if __name__ == "__main__":
    unittest.main()
