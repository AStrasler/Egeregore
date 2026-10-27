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

    def test_latest_event_wins_and_release_ends_the_lease(self) -> None:
        created = NOW - timedelta(hours=2)
        first = self.claim("chatgpt", created, NOW + timedelta(hours=4), 1)
        second = self.claim("cursor", created + timedelta(minutes=5), NOW + timedelta(hours=3), 2)
        self.assertEqual(coord.assess_lease([first, second], NOW).agent, "cursor")

        released = comment("Lease-Release: cursor\n", NOW - timedelta(minutes=1), 3)
        self.assertEqual(coord.assess_lease([second, released], NOW).status, "released")

        renewed = self.claim("cursor", NOW - timedelta(minutes=1), NOW + timedelta(hours=2), 4)
        self.assertEqual(coord.assess_lease([second, released, renewed], NOW).status, "active")

    def test_renewal_keeps_the_earlier_agent(self) -> None:
        created = NOW - timedelta(hours=3)
        claim = self.claim("codex", created, created + timedelta(hours=6), 1)
        renewal = comment(f"Lease-Expires: {iso(NOW + timedelta(hours=2))}\n", NOW - timedelta(minutes=10), 2)
        state = coord.assess_lease([claim, renewal], NOW)
        self.assertEqual(state.status, "active")
        self.assertEqual(state.agent, "codex")

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
    def test_only_the_owner_login_relabels(self) -> None:
        self.assertIsNone(coord.decision_verdict("hello\n"))
        self.assertEqual(coord.decision_verdict("\nDecision: Approve the merge\n"), "approve")
        self.assertIsNone(coord.decision_plan("AStrasler", None, "[Task]: x", []))

        warning = coord.decision_plan("cursor", "approve", "[Task]: Ship it", ["status:needs-owner"])
        assert warning is not None
        self.assertTrue(warning.warning)
        self.assertEqual(warning.add, ())
        warning_comment = coord.render_decision_comment(warning, "9", "approve")
        self.assertNotIn("Lease-Release:", warning_comment)

        approved = coord.decision_plan("AStrasler", "approve", "[Task]: Ship it", ["status:needs-owner"])
        assert approved is not None
        self.assertEqual(approved.add, ("status:ready",))
        self.assertIn("status:needs-owner", approved.remove)
        approved_comment = coord.render_decision_comment(approved, "9", "approve")
        self.assertIn("Lease-Release: aaron", approved_comment)

        rejected = coord.decision_plan("AStrasler", "reject", "[Task]: Ship it", ["status:needs-owner", "status:claimed"])
        assert rejected is not None
        self.assertEqual(rejected.add, ("status:done",))
        self.assertIn("status:claimed", rejected.remove)

        decision_issue = coord.decision_plan(
            "AStrasler",
            "approve",
            "[Decision]: Access",
            ["status:needs-owner", "type:decision"],
        )
        assert decision_issue is not None
        self.assertEqual(decision_issue.add, ("status:done",))
        self.assertNotIn("status:ready", decision_issue.add)

    def test_owner_release_ends_an_active_lease(self) -> None:
        created = NOW - timedelta(hours=1)
        claim = comment(
            f"Claim-Agent: cursor\nLease-Expires: {iso(NOW + timedelta(hours=4))}\n",
            created,
            1,
        )
        plan = coord.decision_plan("AStrasler", "approve", "[Task]: Continue", ["status:needs-owner"])
        assert plan is not None
        release = comment(coord.render_decision_comment(plan, "4", "approve"), NOW, 4)
        self.assertEqual(coord.assess_lease([claim, release], NOW).status, "released")


class CliTests(unittest.TestCase):
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
