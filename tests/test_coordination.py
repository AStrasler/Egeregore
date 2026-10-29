#!/usr/bin/env python3
"""Tests for the Egregore coordination rules. No network."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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

    def test_hygiene_comment_has_no_blank_list_when_nothing_is_wrong_but_owner_is_needed(self) -> None:
        fields = {
            "Project": "weather-intelligence",
            "Decision type": "credentials",
            "Request": "Allow Codex to see the repo.",
            "Why Aaron": "It changes access.",
        }
        check = coord.validate_issue("[Decision]: Repo access", issue_body(fields))
        self.assertTrue(check.ok)
        self.assertTrue(check.needs_owner)
        self.assertEqual(check.problems, ())
        comment_body = coord.render_hygiene_comment(check)
        self.assertEqual(
            comment_body,
            "\n".join(
                [
                    coord.HYGIENE_MARKER,
                    "",
                    "This issue needs Aaron before anyone continues. Filing it does not grant the request.",
                    "",
                    "GitHub does not block the issue on this comment. Edit the issue to update the record.",
                    "",
                    "This comment is the issue-hygiene workflow. It is not an agent claim.",
                ]
            )
            + "\n",
        )
        self.assertNotIn("found problems:", comment_body)
        self.assertNotIn("\n\n\n", comment_body)

    def test_hygiene_comment_keeps_a_problem_list_and_separates_the_owner_note(self) -> None:
        check = coord.IssueCheck(
            ok=False,
            kind="task",
            problems=("Outcome is empty.",),
            needs_owner=True,
            secret=False,
        )
        comment_body = coord.render_hygiene_comment(check)
        self.assertIn("Coordination field check found problems:\n\n- Outcome is empty.", comment_body)
        self.assertIn(
            "- Outcome is empty.\n\nThis issue needs Aaron before anyone continues.",
            comment_body,
        )
        self.assertNotIn("- Outcome is empty.\n\n\n", comment_body)
        self.assertNotRegex(comment_body, r"(?m)^-\s*$")
        self.assertNotIn("\n\n\n", comment_body)


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


class LeaseWatchSummaryTests(unittest.TestCase):
    def claim(self, agent: str, created: datetime, expires: datetime, comment_id: int) -> dict[str, object]:
        body = f"Claim-Agent: {agent}\nLease-Expires: {iso(expires)}\n"
        return comment(body, created, comment_id)

    def run_watch(
        self,
        issues: list[dict[str, object]],
        comments_by_number: dict[int, list[dict[str, object]]],
        drift: set[int] | None = None,
    ) -> tuple[int, str, list[tuple[str, object]]]:
        posts: list[tuple[str, object]] = []
        gets: list[int] = []
        self.gets = gets

        def fake_gh(args: list[str], payload: dict[str, object] | None = None) -> object:
            path = next(arg for arg in args if arg.startswith("repos/"))
            method = args[args.index("--method") + 1]
            if method == "GET" and path.endswith("/issues"):
                return issues
            if method == "GET" and "/issues/" in path and not path.endswith("/comments"):
                number = int(path.rsplit("/", 1)[1])
                gets.append(number)
                if drift is not None and number in drift:
                    comments_by_number[number] = [*comments_by_number[number], comment("a late reply", NOW, 999)]
                return next(item for item in issues if item["number"] == number)
            if method == "PATCH":
                posts.append((path, payload))
                return {}
            if method == "GET" and path.endswith("/comments"):
                number = int(path.split("/")[4])
                return list(comments_by_number[number])
            if method == "POST" and path.endswith("/comments"):
                posts.append((path, payload))
                return {}
            if method == "POST" and "/labels" in path:
                posts.append((path, payload))
                return {}
            if method == "DELETE" and "/labels" in path:
                return {}
            raise AssertionError((method, path))

        with patch.object(coord, "gh_json", fake_gh), patch.dict(
            os.environ, {"GITHUB_REPOSITORY": "AStrasler/Egeregore"}
        ):
            captured = io.StringIO()
            with redirect_stderr(captured):
                code = coord.cmd_check_leases(True, iso(NOW))
        return code, captured.getvalue(), posts

    def test_summary_line_names_the_run_class_and_counts(self) -> None:
        line = coord.lease_watch_summary_line(
            scanned=4, active=2, expired=1, conflicts_warned=1, stalls_flagged=0
        )
        self.assertEqual(
            line,
            "lease-watch: run conflict, issues scanned 4, active 2, expired 1, conflicts warned 1, stalls flagged 0, handoffs expired 0, handoffs held 0, plans stale 0",
        )
        self.assertNotIn("\n", line)
        self.assertEqual(
            coord.lease_watch_run_class(active=1, expired=0, conflicts_warned=0, stalls_flagged=0),
            "active",
        )
        self.assertEqual(
            coord.lease_watch_run_class(active=0, expired=2, conflicts_warned=0, stalls_flagged=1),
            "expired",
        )
        self.assertEqual(
            coord.lease_watch_run_class(active=0, expired=0, conflicts_warned=0, stalls_flagged=1),
            "stall",
        )
        self.assertEqual(
            coord.lease_watch_run_class(active=0, expired=0, conflicts_warned=0, stalls_flagged=0),
            "no-op",
        )

    def test_active_only_run_logs_a_one_line_summary(self) -> None:
        created = NOW - timedelta(hours=1)
        issues = [
            {"number": 7, "labels": [{"name": "status:in-progress"}, {"name": "agent:cursor"}]},
        ]
        comments = {
            7: [self.claim("cursor", created, NOW + timedelta(hours=4), 1)],
        }
        code, log, posts = self.run_watch(issues, comments)
        self.assertEqual(code, 0)
        self.assertEqual(posts, [])
        self.assertEqual(
            log,
            "lease-watch: run active, issues scanned 1, active 1, expired 0, conflicts warned 0, stalls flagged 0, handoffs expired 0, handoffs held 0, plans stale 0\n",
        )

    def test_summary_counts_scans_active_expired_and_new_conflict_warnings(self) -> None:
        created = NOW - timedelta(hours=2)
        active = self.claim("cursor", created, NOW + timedelta(hours=4), 1)
        overlap_holder = self.claim("chatgpt", created, NOW + timedelta(hours=5), 2)
        overlap_other = self.claim("cursor", created + timedelta(minutes=5), NOW + timedelta(hours=3), 3)
        already = coord.assess_lease([overlap_holder, overlap_other], NOW)
        noted = comment(coord.lease_conflict_marker(already) + "\n", NOW - timedelta(minutes=10), 4)
        issues = [
            {"number": 11, "labels": [{"name": "status:claimed"}]},
            {"number": 12, "labels": [{"name": "status:claimed"}]},
            {"number": 13, "labels": [{"name": "status:in-progress"}]},
            {"number": 14, "pull_request": {"url": "https://example.test/pull/14"}},
            {"number": 15, "labels": [{"name": "status:ready"}]},
            {
                "number": 16,
                "labels": [{"name": "status:claimed"}],
            },
        ]
        comments = {
            11: [active],
            12: [self.claim("cursor", created, NOW - timedelta(minutes=1), 5)],
            13: [overlap_holder, overlap_other],
            15: [],
            16: [overlap_holder, overlap_other, noted],
        }
        code, log, posts = self.run_watch(issues, comments)
        self.assertEqual(code, 0)
        conflict_posts = [
            payload
            for _path, payload in posts
            if isinstance(payload, dict) and "Overlapping claim ignored." in str(payload.get("body"))
        ]
        self.assertEqual(len(conflict_posts), 1)
        self.assertIn("issue 12: expired", log)
        self.assertNotIn("issue 11:", log)
        self.assertNotIn("issue 13:", log)
        self.assertNotIn("issue 16:", log)
        self.assertTrue(
            log.endswith(
                "lease-watch: run conflict, issues scanned 5, active 3, expired 1, conflicts warned 1, stalls flagged 0, handoffs expired 0, handoffs held 0, plans stale 0\n"
            )
        )

    def test_live_lease_is_not_flagged_when_the_issue_is_old(self) -> None:
        opened = NOW - timedelta(days=10)
        claimed = NOW - timedelta(hours=1)
        issues = [
            {
                "number": 21,
                "created_at": iso(opened),
                "labels": [{"name": "status:in-progress"}, {"name": "status:needs-review"}],
            }
        ]
        comments = {21: [self.claim("cursor", claimed, NOW + timedelta(hours=4), 1)]}
        code, log, posts = self.run_watch(issues, comments)
        self.assertEqual(code, 0)
        self.assertEqual(posts, [])
        self.assertIn("run active", log)
        self.assertIn("stalls flagged 0, handoffs expired 0, handoffs held 0, plans stale 0", log)

    def test_idle_needs_review_is_flagged_once(self) -> None:
        created = NOW - timedelta(days=4)
        issues = [
            {
                "number": 22,
                "created_at": iso(created),
                "labels": [{"name": "status:needs-review"}],
            }
        ]
        code, log, posts = self.run_watch(issues, {22: []})
        self.assertEqual(code, 0)
        stall_posts = [
            payload
            for _path, payload in posts
            if isinstance(payload, dict) and "<!-- egregore-stall:" in str(payload.get("body"))
        ]
        self.assertEqual(len(stall_posts), 1)
        self.assertIn("Reason: needs-review.", str(stall_posts[0].get("body")))
        label_posts = [
            payload
            for path, payload in posts
            if path.endswith("/labels") and isinstance(payload, dict)
        ]
        self.assertEqual(label_posts, [{"labels": ["stall:flagged"]}])
        self.assertIn("run stall", log)
        self.assertIn("stalls flagged 1", log)

        marker = str(stall_posts[0].get("body"))
        again, again_log, again_posts = self.run_watch(
            [
                {
                    "number": 22,
                    "created_at": iso(created),
                    "labels": [{"name": "status:needs-review"}],
                }
            ],
            {22: [comment(marker, NOW - timedelta(hours=1), 9)]},
        )
        self.assertEqual(again, 0)
        self.assertEqual([payload for _path, payload in again_posts], [{"labels": ["stall:flagged"]}])
        self.assertIn("run no-op", again_log)
        self.assertIn("stalls flagged 0, handoffs expired 0, handoffs held 0, plans stale 0", again_log)

        quiet, quiet_log, quiet_posts = self.run_watch(
            [
                {
                    "number": 22,
                    "created_at": iso(created),
                    "labels": [{"name": "status:needs-review"}, {"name": "stall:flagged"}],
                }
            ],
            {22: [comment(marker, NOW - timedelta(hours=1), 9)]},
        )
        self.assertEqual(quiet, 0)
        self.assertEqual(quiet_posts, [])
        self.assertIn("run no-op", quiet_log)

    def test_fresh_lease_backstop_is_activity_so_the_same_run_does_not_stall(self) -> None:
        opened = NOW - timedelta(days=10)
        claimed = NOW - timedelta(days=5)
        issues = [
            {
                "number": 23,
                "created_at": iso(opened),
                "labels": [{"name": "status:claimed"}, {"name": "status:needs-owner"}],
            }
        ]
        comments = {23: [self.claim("cursor", claimed, claimed + timedelta(hours=4), 1)]}
        code, log, posts = self.run_watch(issues, comments)
        self.assertEqual(code, 0)
        bodies = [
            str(payload.get("body"))
            for _path, payload in posts
            if isinstance(payload, dict) and payload.get("body")
        ]
        self.assertEqual(sum("Lease backstop." in body for body in bodies), 1)
        self.assertEqual(sum("<!-- egregore-stall:" in body for body in bodies), 0)
        self.assertIn("stalls flagged 0, handoffs expired 0, handoffs held 0, plans stale 0", log)
        self.assertIn("run expired", log)

        backstop = next(body for body in bodies if "Lease backstop." in body)
        later, later_log, later_posts = self.run_watch(
            [
                {
                    "number": 23,
                    "created_at": iso(opened),
                    "labels": [
                        {"name": "status:needs-owner"},
                        {"name": "lease:expired"},
                    ],
                }
            ],
            {23: [comment(backstop, NOW - timedelta(days=4), 2)]},
        )
        self.assertEqual(later, 0)
        stall_posts = [
            payload
            for _path, payload in later_posts
            if isinstance(payload, dict) and "<!-- egregore-stall:" in str(payload.get("body"))
        ]
        self.assertEqual(len(stall_posts), 1)
        self.assertIn(f"<!-- egregore-stall:{iso(NOW - timedelta(days=4))} -->", str(stall_posts[0].get("body")))
        self.assertIn("stalls flagged 1", later_log)


class FormLabelAndStallTests(unittest.TestCase):
    def test_form_labels_replace_a_stale_set_and_then_match(self) -> None:
        title = "[Task]: Docs"
        body = issue_body(task_fields(Project="whoop-lens", Role="codex", Priority="high"))
        stale = ["project:coordination", "priority:low", "agent:cursor", "status:ready"]
        plan = coord.form_label_updates(title, body, stale)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.add, ("project:whoop-lens", "priority:high", "agent:codex"))
        self.assertEqual(plan.remove, ("project:coordination", "priority:low", "agent:cursor"))
        updated = ["status:ready", "project:whoop-lens", "priority:high", "agent:codex"]
        self.assertIsNone(coord.form_label_updates(title, body, updated))

    def test_role_any_does_not_set_or_clear_an_agent_label(self) -> None:
        body = issue_body(task_fields(Role="any"))
        self.assertIsNone(
            coord.form_label_updates(
                "[Task]: Docs",
                body,
                ["project:coordination", "priority:normal", "agent:cursor"],
            )
        )

    def test_handoff_to_and_decision_owner_set_the_agent_label(self) -> None:
        handoff = coord.form_label_updates(
            "[Handoff]: Next",
            issue_body({"Project": "coordination", "To": "grokbot"}),
            ["agent:cursor"],
        )
        self.assertIsNotNone(handoff)
        assert handoff is not None
        self.assertEqual(handoff.add, ("project:coordination", "agent:grokbot"))
        self.assertEqual(handoff.remove, ("agent:cursor",))
        decision = coord.form_label_updates(
            "[Decision]: Merge the docs",
            issue_body({"Project": "coordination"}),
            ["agent:cursor", "project:coordination"],
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.add, ("agent:aaron",))
        self.assertEqual(decision.remove, ("agent:cursor",))
        self.assertIsNone(
            coord.form_label_updates(
                "[Decision]: Merge the docs",
                issue_body({"Project": "coordination"}),
                ["project:coordination", "agent:aaron"],
            )
        )

    def test_duplicate_task_titles_skip_self_and_non_tasks(self) -> None:
        title = "[Task]: Align the operating docs with the pilot"
        close = "[Task]: Align the operating docs with the pilots"
        self.assertTrue(coord.titles_are_close(title, close))
        self.assertFalse(coord.titles_are_close("[Task]: Docs", "[Task]: Dock"))
        problems = coord.duplicate_task_problems(
            title,
            [
                (4, title),
                (8, "[Handoff]: Align the operating docs with the pilot"),
                (9, close),
                (10, "[Task]: Record the weather station battery replacement"),
                (11, close),
                (12, close),
                (13, close),
            ],
            number=4,
        )
        self.assertEqual(
            problems,
            (
                "Title closely matches open task #9.",
                "Title closely matches open task #11.",
                "Title closely matches open task #12.",
            ),
        )
        self.assertEqual(coord.duplicate_task_problems("[Handoff]: Next", [(9, close)]), ())

    def test_closed_task_rewrite_and_flag_modes(self) -> None:
        labels = ["status:ready", "status:claimed", "type:task"]
        plan, message = coord.closed_task_plan("[Task]: Docs", labels, closed=True, mode="rewrite")
        self.assertIsNone(message)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.add, ("status:done",))
        self.assertEqual(plan.remove, ("status:ready", "status:claimed"))
        flagged, note = coord.closed_task_plan("[Task]: Docs", labels, closed=True, mode="flag")
        self.assertIsNone(flagged)
        self.assertEqual(note, "This closed task is missing status:done.")
        self.assertEqual(
            coord.closed_task_plan(
                "[Task]: Docs",
                ["status:done", "status:ready"],
                closed=True,
                mode="flag",
            ),
            (None, None),
        )
        self.assertEqual(
            coord.closed_task_plan("[Task]: Docs", ["status:done"], closed=True, mode="rewrite"),
            (None, None),
        )
        self.assertEqual(
            coord.closed_task_plan("[Handoff]: Next", labels, closed=True, mode="rewrite"),
            (None, None),
        )
        self.assertEqual(
            coord.closed_task_plan("[Task]: Docs", labels, closed=False, mode="rewrite"),
            (None, None),
        )
        self.assertEqual(coord.closed_status_mode("flag"), "flag")
        self.assertEqual(coord.closed_status_mode("FLAG"), "flag")
        self.assertEqual(coord.closed_status_mode("rewrite"), "rewrite")

    def test_stall_plan_waits_for_a_live_lease_and_does_not_repeat(self) -> None:
        created = NOW - timedelta(days=5)
        labels = ["status:needs-owner", "type:handoff"]
        body, add_label = coord.stall_plan(labels, created, [], NOW, lease_active=False)
        self.assertIsNotNone(body)
        assert body is not None
        self.assertIn(f"<!-- egregore-stall:{iso(created)} -->", body)
        self.assertNotIn("handoff+needs-owner:", body)
        self.assertIn("Reason: handoff+needs-owner.", body)
        self.assertTrue(add_label)
        self.assertEqual(
            coord.stall_plan(labels, created, [], NOW, lease_active=True),
            (None, False),
        )
        self.assertEqual(
            coord.stall_plan(labels, NOW - timedelta(days=1), [], NOW, lease_active=False),
            (None, False),
        )
        self.assertEqual(
            coord.stall_plan(["status:done", "type:handoff"], created, [], NOW, lease_active=False),
            (None, False),
        )
        self.assertEqual(coord.stall_plan(labels, None, [], NOW, lease_active=False), (None, False))
        noted = comment(body, NOW - timedelta(hours=2), 3)
        self.assertEqual(
            coord.stall_plan(labels, created, [noted], NOW, lease_active=False),
            (None, True),
        )
        owner_only, owner_label = coord.stall_plan(
            ["status:needs-owner"],
            created,
            [noted],
            NOW,
            lease_active=False,
        )
        self.assertIsNone(owner_only)
        self.assertTrue(owner_label)
        older = comment(
            f"<!-- egregore-stall:needs-review:{iso(created)} -->\n\nStall flag.\n",
            NOW - timedelta(hours=3),
            5,
        )
        changed, changed_label = coord.stall_plan(
            ["status:needs-owner"],
            created,
            [older],
            NOW,
            lease_active=False,
        )
        self.assertIsNone(changed)
        self.assertTrue(changed_label)
        self.assertEqual(
            coord.stall_plan(
                labels + ["stall:flagged"],
                created,
                [noted],
                NOW,
                lease_active=False,
            ),
            (None, False),
        )
        newer = comment("Still working.\n", NOW - timedelta(hours=1), 4)
        self.assertEqual(
            coord.stall_plan(labels, created, [noted, newer], NOW, lease_active=False),
            (None, False),
        )


class HygieneApplyTests(unittest.TestCase):
    def apply_issue(
        self,
        issue: dict[str, object],
        open_issues: list[dict[str, object]],
        *,
        env: dict[str, str] | None = None,
        comments: list[dict[str, object]] | None = None,
    ) -> tuple[list[tuple[str, str, object]], dict[str, object]]:
        store = {
            "labels": [str(item["name"]) for item in issue.get("labels") or [] if isinstance(item, dict)],
            "comments": list(comments or []),
        }
        calls: list[tuple[str, str, object]] = []

        def fake_gh(args: list[str], payload: dict[str, object] | None = None) -> object:
            path = next(arg for arg in args if arg.startswith("repos/"))
            method = args[args.index("--method") + 1]
            calls.append((method, path, payload))
            if method == "GET" and path.endswith(f"/issues/{issue['number']}"):
                return {
                    "number": issue["number"],
                    "title": issue["title"],
                    "body": issue["body"],
                    "state": issue.get("state", "open"),
                    "labels": [{"name": name} for name in store["labels"]],
                }
            if method == "GET" and path.endswith("/issues"):
                return open_issues
            if method == "GET" and path.endswith("/comments"):
                return list(store["comments"])
            if method == "POST" and path.endswith("/comments"):
                store["comments"].append({"id": len(store["comments"]) + 1, "body": payload["body"] if payload else ""})
                return {}
            if method == "PATCH" and "/comments/" in path:
                return {}
            if method == "POST" and path.endswith("/labels"):
                for name in (payload or {}).get("labels") or []:
                    if name not in store["labels"]:
                        store["labels"].append(str(name))
                return {}
            if method == "DELETE" and "/labels/" in path:
                name = unquote(path.rsplit("/", 1)[-1])
                if name in store["labels"]:
                    store["labels"].remove(name)
                return {}
            raise AssertionError((method, path))

        values = {"GITHUB_REPOSITORY": "AStrasler/Egeregore", "ISSUE_NUMBER": str(issue["number"])}
        if env:
            values.update(env)
        with patch.object(coord, "gh_json", fake_gh), patch.dict(os.environ, values, clear=False):
            if "EGREGORE_CLOSED_STATUS" not in values:
                os.environ.pop("EGREGORE_CLOSED_STATUS", None)
            code = coord.cmd_check_issue(True, None, None)
        self.assertEqual(code, 0)
        return calls, store

    def test_apply_syncs_form_labels_and_warns_for_a_close_title(self) -> None:
        title = "[Task]: Align the operating docs with the pilot"
        body = issue_body(task_fields())
        issue = {
            "number": 30,
            "title": title,
            "body": body,
            "state": "open",
            "labels": [{"name": "type:task"}, {"name": "status:ready"}, {"name": "agent:grokbot"}],
        }
        others = [
            {"number": 30, "title": title},
            {"number": 31, "title": "[Task]: Align the operating docs with the pilots"},
            {"number": 32, "title": "[Handoff]: Align the operating docs with the pilot", "pull_request": {}},
        ]
        calls, store = self.apply_issue(issue, others)
        label_adds = [payload for method, path, payload in calls if method == "POST" and path.endswith("/labels")]
        self.assertEqual(
            label_adds,
            [{"labels": ["project:coordination", "priority:normal", "agent:cursor"]}],
        )
        removed = [unquote(path.rsplit("/", 1)[-1]) for method, path, _payload in calls if method == "DELETE"]
        self.assertEqual(removed, ["agent:grokbot"])
        comments = [payload for method, path, payload in calls if method == "POST" and path.endswith("/comments")]
        self.assertEqual(len(comments), 1)
        self.assertIn("Title closely matches open task #31.", str(comments[0]["body"]))
        self.assertNotIn("#32", str(comments[0]["body"]))
        again, again_store = self.apply_issue(
            {
                **issue,
                "labels": [{"name": name} for name in store["labels"]],
            },
            others,
            comments=list(store["comments"]),
        )
        self.assertEqual(
            [call for call in again if call[0] in {"POST", "PATCH", "DELETE"}],
            [],
        )
        self.assertEqual(again_store["comments"], store["comments"])

    def test_closed_task_rewrite_sets_done_and_flag_mode_only_comments(self) -> None:
        issue = {
            "number": 40,
            "title": "[Task]: Docs",
            "body": issue_body(task_fields()),
            "state": "closed",
            "labels": [
                {"name": "type:task"},
                {"name": "status:claimed"},
                {"name": "status:ready"},
                {"name": "project:coordination"},
                {"name": "priority:normal"},
                {"name": "agent:cursor"},
            ],
        }
        calls, store = self.apply_issue(issue, [])
        removed = [unquote(path.rsplit("/", 1)[-1]) for method, path, _payload in calls if method == "DELETE"]
        self.assertEqual(removed, ["status:ready", "status:claimed"])
        added = [payload for method, path, payload in calls if method == "POST" and str(path).endswith("/labels")]
        self.assertEqual(added, [{"labels": ["status:done"]}])
        self.assertNotIn(
            "POST",
            [method for method, path, _payload in calls if path.endswith("/comments")],
        )
        self.assertEqual(store["labels"], ["type:task", "project:coordination", "priority:normal", "agent:cursor", "status:done"])

        flagged, flagged_store = self.apply_issue(issue, [], env={"EGREGORE_CLOSED_STATUS": "flag"})
        self.assertEqual(
            [call for call in flagged if call[0] in {"POST", "DELETE"} and "/labels" in call[1]],
            [],
        )
        notes = [payload for method, path, payload in flagged if method == "POST" and path.endswith("/comments")]
        self.assertEqual(len(notes), 1)
        self.assertIn("This closed task is missing status:done.", str(notes[0]["body"]))
        self.assertIn("status:claimed", flagged_store["labels"])


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
            "stall:flagged",
        ):
            self.assertIn(label, names)
        self.assertNotIn("lease:active", names)

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
            allowed_actions = {
                "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            }
            if path.name == "ci.yml":
                # The worker job needs Node. Pinned by full commit SHA, like checkout.
                allowed_actions.add("actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1")
            for use in uses:
                self.assertIn(use, allowed_actions)
            permissions = permission_lines(text)
            self.assertIn("contents: read", permissions)
            if path.name == "ci.yml":
                self.assertEqual(permissions, ["contents: read"])
            else:
                self.assertEqual(permissions, ["contents: read", "issues: write"])

        hygiene = (workflow_dir / "issue-hygiene.yml").read_text(encoding="utf-8")
        self.assertIn("startsWith(github.event.comment.body, 'Decision:')", hygiene)
        self.assertIn("types: [opened, edited, closed]", hygiene)
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
        self.assertIn("mcp/README.md", readme)
        self.assertIn("no Cloudflare, Vercel, or Supabase service", readme)
        self.assertIn("WHOOP-Lens", onboarding)
        self.assertIn("does not grant", onboarding)


def handoff_issue(number: int, intent: str | None, age_days: int, labels: list[str] | None = None) -> dict[str, object]:
    body = "### Project\n\ncoordination\n\n"
    if intent is not None:
        body += f"### Intent\n\n{intent}\n\n"
    body += "### Next action\n\nDo it.\n"
    return {
        "number": number,
        "title": "[Handoff]: pass it on",
        "body": body,
        "state": "open",
        "created_at": iso(NOW - timedelta(days=age_days)),
        "labels": [{"name": name} for name in (labels or ["type:handoff", "status:ready"])],
    }


class HandoffLifecycleTests(unittest.TestCase):
    def plan(self, issue: dict[str, object], comments: list[dict[str, object]], **kw: object) -> coord.HandoffPlan:
        return coord.handoff_plan(
            str(issue["title"]),
            coord.label_names(issue["labels"]),  # type: ignore[arg-type]
            str(issue["body"]),
            coord.try_parse_time(str(issue["created_at"])),
            comments,
            NOW,
            lease_active=bool(kw.get("lease_active", False)),
            mode=str(kw.get("mode", "flag")),
        )

    def test_intent_is_read_from_the_form_field(self) -> None:
        self.assertEqual(coord.handoff_intent(str(handoff_issue(1, "fyi", 1)["body"])), "fyi")
        self.assertEqual(coord.handoff_intent(str(handoff_issue(1, "Action", 1)["body"])), "action")
        self.assertEqual(coord.handoff_intent(str(handoff_issue(1, "feedback", 1)["body"])), "feedback")
        self.assertEqual(coord.handoff_intent(str(handoff_issue(1, None, 1)["body"])), "unknown")
        self.assertEqual(coord.handoff_intent(str(handoff_issue(1, "whatever", 1)["body"])), "unknown")

    def test_a_fyi_handoff_expires_after_fourteen_days_and_not_before(self) -> None:
        young = self.plan(handoff_issue(1, "fyi", 13), [])
        self.assertEqual(young.outcome, "none")
        old = self.plan(handoff_issue(1, "fyi", 14), [])
        self.assertEqual(old.outcome, "expire")
        self.assertFalse(old.close)
        assert old.comment
        self.assertIn("<!-- egregore-handoff-expiry:", old.comment)
        self.assertIn("does not mark the work done", old.comment)
        self.assertNotIn("status:done", old.comment)

    def test_close_mode_closes_as_not_planned_and_never_marks_done(self) -> None:
        plan = self.plan(handoff_issue(1, "fyi", 30), [], mode="close")
        self.assertEqual((plan.outcome, plan.close), ("expire", True))
        self.assertEqual(coord.handoff_expiry_mode("close"), "close")
        self.assertEqual(coord.handoff_expiry_mode("anything else"), "flag")
        self.assertEqual(coord.handoff_expiry_mode(""), "flag")

    def test_an_untouched_handoff_expires_whatever_its_intent(self) -> None:
        workflow_only = [comment(coord.HYGIENE_MARKER + "\nHygiene ok", NOW - timedelta(days=20), 1)]
        for intent in ("action", "feedback", "fyi", None):
            self.assertEqual(self.plan(handoff_issue(1, intent, 20), []).outcome, "expire", intent)
            self.assertEqual(self.plan(handoff_issue(1, intent, 20), workflow_only).outcome, "expire", intent)

    def test_picked_up_action_and_feedback_without_evidence_stay_open_for_a_person(self) -> None:
        for intent in ("action", "feedback", None):
            for age in (14, 60, 400):
                touched = [comment("Looking at this.", NOW - timedelta(days=age), 1)]
                plan = self.plan(handoff_issue(1, intent, age), touched)
                self.assertEqual(plan.outcome, "hold", (intent, age))
                self.assertIsNone(plan.comment)
                self.assertFalse(plan.close)
        claim = [
            comment(
                f"Claim-Agent: cursor\nLease-Expires: {iso(NOW - timedelta(days=20))}\n",
                NOW - timedelta(days=21),
                1,
            )
        ]
        self.assertEqual(self.plan(handoff_issue(1, "action", 21), claim).outcome, "hold")

    def test_evidence_does_not_let_age_close_it_either(self) -> None:
        evidence = [comment("Agent: Cursor\n\n## Evidence\nShipped.", NOW - timedelta(days=20), 1)]
        plan = self.plan(handoff_issue(1, "action", 90), evidence)
        self.assertEqual(plan.outcome, "none")
        self.assertIsNone(plan.comment)
        self.assertFalse(plan.close)

    def test_age_alone_never_sets_done_for_any_age_or_intent(self) -> None:
        for intent in ("action", "feedback", "fyi", None):
            for age in (1, 14, 100, 5000):
                plan = self.plan(handoff_issue(1, intent, age), [])
                text = (plan.comment or "") + plan.reason
                self.assertNotIn("status:done", text)
                self.assertIn(plan.outcome, ("none", "expire", "hold"))

    def test_other_kinds_done_handoffs_and_live_leases_are_left_alone(self) -> None:
        task = handoff_issue(1, "fyi", 90)
        task["title"] = "[Task]: not a handoff"
        task["labels"] = [{"name": "type:task"}]
        self.assertEqual(self.plan(task, []).outcome, "none")
        done = handoff_issue(2, "fyi", 90, ["type:handoff", "status:done"])
        self.assertEqual(self.plan(done, []).outcome, "none")
        self.assertEqual(self.plan(handoff_issue(3, "fyi", 90), [], lease_active=True).outcome, "none")

    def test_the_expiry_note_does_not_restart_the_window_or_repeat(self) -> None:
        first = self.plan(handoff_issue(1, "fyi", 20), [])
        assert first.comment
        noted = [comment(first.comment, NOW - timedelta(hours=1), 5)]
        again = self.plan(handoff_issue(1, "fyi", 20), noted)
        self.assertEqual(again.outcome, "none")
        self.assertEqual(again.reason, "expiry already noted")
        # A newer comment from a person opens a new window.
        replied = noted + [comment("Still relevant.", NOW - timedelta(days=2), 6)]
        self.assertEqual(self.plan(handoff_issue(1, "fyi", 20), replied).outcome, "none")

    def test_close_mode_still_closes_a_handoff_already_flagged_in_flag_mode(self) -> None:
        flagged = self.plan(handoff_issue(1, "fyi", 20), [])
        assert flagged.comment
        noted = [comment(flagged.comment, NOW - timedelta(hours=1), 5)]
        self.assertEqual(self.plan(handoff_issue(1, "fyi", 20), noted).outcome, "none")
        closing = self.plan(handoff_issue(1, "fyi", 20), noted, mode="close")
        self.assertEqual((closing.outcome, closing.close), ("expire", True))
        assert closing.comment
        self.assertIn(coord.CLOSING_NOTE, closing.comment)

    def test_a_posted_closing_note_with_a_failed_close_retries_the_close_without_a_second_note(self) -> None:
        first = self.plan(handoff_issue(1, "fyi", 20), [], mode="close")
        assert first.comment
        noted = [comment(first.comment, NOW - timedelta(hours=1), 5)]
        retry = self.plan(handoff_issue(1, "fyi", 20), noted, mode="close")
        self.assertEqual((retry.outcome, retry.close, retry.comment), ("expire", True, None))
        # Back in flag mode the same thread asks for nothing.
        self.assertEqual(self.plan(handoff_issue(1, "fyi", 20), noted).outcome, "none")

    def test_an_fyi_handoff_with_evidence_is_left_for_a_person_even_in_close_mode(self) -> None:
        evidence = [comment("Agent: Cursor\n\n## Evidence\nShipped.", NOW - timedelta(days=20), 1)]
        for mode in ("flag", "close"):
            plan = self.plan(handoff_issue(1, "fyi", 40), evidence, mode=mode)
            self.assertEqual((plan.outcome, plan.comment, plan.close), ("none", None, False), mode)

    def test_the_expiry_note_does_not_restart_the_stall_clock(self) -> None:
        issue = handoff_issue(1, "fyi", 20)
        first = coord.scan_issue(issue, [], NOW)
        bodies = [step.body for step in first.steps if step.kind == "comment"]
        self.assertEqual(len(bodies), 2)
        posted = [comment(body, NOW - timedelta(days=2), index + 1) for index, body in enumerate(bodies)]
        # Four days on, nothing new was said by anyone. No second stall flag, no second expiry note.
        later = coord.scan_issue(issue, posted, NOW + timedelta(days=4))
        self.assertEqual([step.counts for step in later.steps if step.kind == "comment"], [])


class ScanThenApplyTests(unittest.TestCase):
    run_watch = LeaseWatchSummaryTests.run_watch
    claim = LeaseWatchSummaryTests.claim

    def test_scan_writes_nothing_and_the_plan_lists_every_write(self) -> None:
        issue = handoff_issue(31, "fyi", 20)
        plan = coord.scan_issue(issue, [], NOW)
        # The expiry note, and the stall flag the same idle handoff already earns.
        self.assertEqual([step.counts for step in plan.steps if step.kind == "comment"], ["stall", "handoff_expired"])
        self.assertEqual(plan.handoff, "expire")
        self.assertEqual(
            coord.lease_watch_run_class(active=1, expired=0, conflicts_warned=0, stalls_flagged=0, handoffs_expired=1),
            "handoff",
        )

    def test_a_fyi_handoff_is_flagged_through_the_watch_and_never_marked_done(self) -> None:
        issues = [handoff_issue(31, "fyi", 20)]
        code, log, posts = self.run_watch(issues, {31: []})
        self.assertEqual(code, 0)
        bodies = [str(payload.get("body")) for _p, payload in posts if isinstance(payload, dict) and "body" in payload]
        self.assertEqual(len([b for b in bodies if "egregore-handoff-expiry" in b]), 1)
        self.assertFalse(any(path for path, _ in posts if "PATCH" in path))
        self.assertNotIn("status:done", "".join(str(payload) for _p, payload in posts if not str(payload).startswith("{'body'")))
        self.assertIn("handoffs expired 1", log)

    def test_close_mode_patches_the_issue_closed_as_not_planned(self) -> None:
        issues = [handoff_issue(32, "fyi", 20)]
        with patch.dict(os.environ, {"EGREGORE_HANDOFF_EXPIRY": "close"}):
            code, log, posts = self.run_watch(issues, {32: []})
        self.assertEqual(code, 0)
        patched = [payload for path, payload in posts if path.endswith("/issues/32")]
        self.assertEqual(patched, [{"state": "closed", "state_reason": "not_planned"}])

    def test_close_mode_retries_only_the_close_when_the_note_already_exists(self) -> None:
        first = coord.handoff_plan(
            "[Handoff]: x", ["type:handoff"], str(handoff_issue(34, "fyi", 20)["body"]),
            NOW - timedelta(days=20), [], NOW, lease_active=False, mode="close",
        )
        assert first.comment
        issues = [handoff_issue(34, "fyi", 20)]
        thread = {34: [comment(first.comment, NOW - timedelta(hours=1), 9)]}
        with patch.dict(os.environ, {"EGREGORE_HANDOFF_EXPIRY": "close"}):
            code, log, posts = self.run_watch(issues, thread)
        self.assertEqual(code, 0)
        bodies = [str(payload.get("body")) for _p, payload in posts if isinstance(payload, dict) and "body" in payload]
        self.assertFalse(any("egregore-handoff-expiry" in b for b in bodies))
        patched = [payload for path, payload in posts if path.endswith("/issues/34")]
        self.assertEqual(patched, [{"state": "closed", "state_reason": "not_planned"}])
        self.assertIn("handoffs expired 1", log)

    def test_a_picked_up_action_handoff_is_held_and_counted_without_a_handoff_write(self) -> None:
        issues = [handoff_issue(33, "action", 40)]
        code, log, posts = self.run_watch(issues, {33: [comment("On it.", NOW - timedelta(days=39), 1)]})
        self.assertEqual(code, 0)
        bodies = [str(payload.get("body")) for _p, payload in posts if isinstance(payload, dict) and "body" in payload]
        self.assertFalse(any("egregore-handoff-expiry" in b for b in bodies))
        self.assertIn("handoffs held 1", log)
        self.assertIn("handoffs expired 0", log)

    def test_a_plan_is_dropped_when_the_thread_changed_after_the_scan(self) -> None:
        issues = [handoff_issue(34, "fyi", 20), handoff_issue(35, "fyi", 20)]
        code, log, posts = self.run_watch(issues, {34: [], 35: []}, drift={34})
        self.assertEqual(code, 0)
        targets = [path for path, _ in posts]
        self.assertFalse(any("/issues/34/" in path for path in targets), targets)
        self.assertTrue(any("/issues/35/comments" in path for path in targets), targets)
        self.assertIn("issue 34: changed since the scan, plan dropped", log)
        self.assertIn("plans stale 1", log)
        self.assertIn("handoffs expired 1", log)

    def test_an_issue_with_nothing_to_write_is_not_read_again(self) -> None:
        created = NOW - timedelta(hours=1)
        issues = [{"number": 7, "labels": [{"name": "status:in-progress"}]}]
        self.run_watch(issues, {7: [self.claim("cursor", created, NOW + timedelta(hours=4), 1)]})
        self.assertEqual(self.gets, [])


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
