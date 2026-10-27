#!/usr/bin/env python3
"""GitHub coordination checks for the Egregore repo.

Library functions are pure and do not touch the network. The CLI uses `gh`
only when `--apply` is set, which is how the workflows run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import quote

HYGIENE_MARKER = "<!-- egregore-hygiene -->"
BOUNDARY_SENTENCE = (
    "Coordination only. No project data, health data, credentials, "
    "or another person's personal information."
)
ALLOWED_AGENTS = frozenset({"chatgpt", "grokbot", "cursor", "codex", "aaron"})
TASK_ROLES = ALLOWED_AGENTS | {"any"}
PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
RISKS = frozenset({"none", "auth or permissions", "data handling", "deployment", "other"})
EVIDENCE_RESULTS = frozenset({"progress", "blocked", "ready-for-review", "complete"})
DECISION_TYPES = frozenset(
    {
        "merge",
        "deployment",
        "credentials",
        "permission or access",
        "new project",
        "repository setting",
        "other",
    }
)
STATUS_LABELS = (
    "status:ready",
    "status:claimed",
    "status:in-progress",
    "status:blocked",
    "status:needs-review",
    "status:needs-owner",
    "status:done",
)
ACTIVE_LEASE_LABELS = frozenset({"status:claimed", "status:in-progress"})
OWNER_LOGIN = "AStrasler"
MAX_LEASE = timedelta(hours=24)

REQUIRED_SECTIONS = {
    "task": (
        "Project",
        "Role",
        "Priority",
        "Outcome",
        "Context",
        "Out of scope",
        "Approval needed",
    ),
    "handoff": (
        "Project",
        "From",
        "To",
        "Related issue",
        "Done so far",
        "Next action",
        "Access",
    ),
    "evidence": ("Project", "Related issue", "What happened", "Links", "Result"),
    "review": ("Project", "Pull request", "What to verify", "Risk"),
    "decision": ("Project", "Decision type", "Request", "Why Aaron"),
}

SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
)
HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")
EXPIRES_RE = re.compile(r"^Lease-Expires:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*$")
CLAIM_AGENT_RE = re.compile(r"^Claim-Agent:\s*([A-Za-z]+)\s*$")
RELEASE_RE = re.compile(r"^Lease-Release:\s*([A-Za-z]+)\s*$")
DECISION_RE = re.compile(r"^Decision:\s*(approve|reject)\b", re.IGNORECASE)
BOUNDARY_LINE_RE = re.compile(r"^Data-Boundary:\s*coordination-only\s*$", re.IGNORECASE)
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class IssueCheck:
    ok: bool
    kind: str | None
    problems: tuple[str, ...]
    needs_owner: bool
    secret: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LeaseState:
    """The lease that is current after reading the thread.

    lease_id is the comment id of the claim that started this hold. Renewals
    keep it. A later hold, after release or expiry, gets a new one.
    """

    status: str
    agent: str | None
    expires: datetime | None
    marker: str
    conflicts: tuple[str, ...] = ()
    lease_id: str | None = None


@dataclass(frozen=True)
class LabelPlan:
    add: tuple[str, ...]
    remove: tuple[str, ...]
    marker: str
    reason: str


@dataclass(frozen=True)
class DecisionPlan:
    """A Decision comment is a recorded note, never a verified authorization.

    authorization is "none" or "recorded". This module does not emit "verified":
    a GitHub login cannot prove Aaron personally approved, because an agent can
    post through his connected account.
    """

    warning: bool
    add: tuple[str, ...]
    remove: tuple[str, ...]
    note: str
    authorization: str


class GhError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    @property
    def missing_label(self) -> bool:
        text = self.message.lower()
        return "422" in text or ("label" in text and "not found" in text)

    @property
    def missing_resource(self) -> bool:
        return "404" in self.message


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"time must include a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def try_parse_time(value: str) -> datetime | None:
    try:
        return parse_time(value)
    except (ValueError, TypeError):
        return None


def kind_from_title(title: str) -> str | None:
    folded = title.strip().lower()
    prefixes = {
        "[task]": "task",
        "[handoff]": "handoff",
        "[evidence]": "evidence",
        "[review]": "review",
        "[decision]": "decision",
    }
    for prefix, kind in prefixes.items():
        if folded.startswith(prefix):
            return kind
    return None


def sections(body: str) -> dict[str, str]:
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.replace("\r\n", "\n").split("\n"):
        heading = HEADING_RE.match(line)
        if heading:
            current = heading.group(1).strip()
            found.setdefault(current, [])
            continue
        if current is not None:
            found[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in found.items()}


def first_line(value: str | None) -> str:
    if not value:
        return ""
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def present(value: str | None) -> bool:
    text = (value or "").strip()
    return bool(text) and text != "_No response_"


def boundary_state(body: str) -> str:
    """Return checked, unchecked, or missing."""
    saw_sentence = False
    for line in body.replace("\r\n", "\n").split("\n"):
        if BOUNDARY_LINE_RE.match(line.strip()):
            return "checked"
        if BOUNDARY_SENTENCE in line:
            saw_sentence = True
            if re.search(r"\[[xX]\]", line):
                return "checked"
    return "unchecked" if saw_sentence else "missing"


def looks_like_secret(body: str) -> bool:
    return any(pattern.search(body) for pattern in SECRET_PATTERNS)


def redact(text: str) -> str:
    cleaned = text
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    first = cleaned.strip().splitlines()
    return (first[0] if first else "gh api failed")[:300]


def validate_issue(title: str, body: str) -> IssueCheck:
    kind = kind_from_title(title or "")
    parsed = sections(body or "")
    problems: list[str] = []
    needs_owner = False

    boundary = boundary_state(body or "")
    if boundary == "missing":
        problems.append("Data boundary confirmation is missing.")
    elif boundary == "unchecked":
        problems.append("Data boundary checkbox is unchecked.")

    if looks_like_secret(body or ""):
        problems.append(
            "A credential-like string is present. Remove it and rotate the credential. "
            "The value was not copied into this comment. This repository is public, so a "
            "saved value may already be exposed."
        )
        needs_owner = True

    if kind is None:
        problems.append(
            "Title must start with [Task]:, [Handoff]:, [Evidence]:, [Review]:, or [Decision]:."
        )
    else:
        for heading in REQUIRED_SECTIONS[kind]:
            if not present(parsed.get(heading)):
                problems.append(f"{heading} is empty.")

    project = first_line(parsed.get("Project"))
    if project == "other" and not present(parsed.get("Project slug")):
        problems.append("Project slug is required when Project is other.")

    if kind == "task" and present(parsed.get("Role")):
        role = first_line(parsed.get("Role")).lower()
        if role not in TASK_ROLES:
            problems.append("Role must be chatgpt, grokbot, cursor, codex, aaron, or any.")
    if kind == "task" and present(parsed.get("Priority")):
        priority = first_line(parsed.get("Priority")).lower()
        if priority not in PRIORITIES:
            problems.append("Priority must be low, normal, high, or urgent.")
    if kind == "task" and present(parsed.get("Approval needed")):
        approval = first_line(parsed.get("Approval needed")).lower()
        if approval.startswith("yes"):
            needs_owner = True
        elif not approval.startswith("no"):
            problems.append("Approval needed must start with yes or no.")

    if kind == "handoff":
        for heading in ("From", "To"):
            if not present(parsed.get(heading)):
                continue
            agent = first_line(parsed.get(heading)).lower()
            if agent not in ALLOWED_AGENTS:
                problems.append(f"{heading} must name chatgpt, grokbot, cursor, codex, or aaron.")
        access = parsed.get("Access") or ""
        if present(access) and "does not grant" not in access.lower():
            problems.append("Access must say that the handoff does not grant access.")

    if kind == "review" and present(parsed.get("Risk")):
        risk = first_line(parsed.get("Risk")).lower()
        if risk not in RISKS:
            problems.append(
                "Risk must be none, auth or permissions, data handling, deployment, or other."
            )

    if kind == "evidence" and present(parsed.get("Result")):
        result = first_line(parsed.get("Result")).lower()
        if result not in EVIDENCE_RESULTS:
            problems.append("Result must be progress, blocked, ready-for-review, or complete.")

    if kind == "decision":
        needs_owner = True
        if present(parsed.get("Decision type")):
            decision_type = first_line(parsed.get("Decision type")).lower()
            if decision_type not in DECISION_TYPES:
                problems.append("Decision type is not a known value.")

    secret = looks_like_secret(body or "")
    return IssueCheck(
        ok=not problems,
        kind=kind,
        problems=tuple(problems),
        needs_owner=needs_owner,
        secret=secret,
    )


def render_hygiene_comment(check: IssueCheck, *, passed: bool = False) -> str:
    lines = [HYGIENE_MARKER, ""]
    if passed or (check.ok and not check.needs_owner):
        lines.append("Coordination field check passed.")
    else:
        if check.problems:
            lines.append("Coordination field check found problems:")
            lines.append("")
            for problem in check.problems:
                lines.append(f"- {problem}")
        if check.needs_owner:
            # A blank line separates a problem list from the next paragraph.
            # With nothing to list, that blank line is a stray gap (seen on a
            # needs-owner issue that had no field problems).
            if check.problems:
                lines.append("")
            lines.append(
                "This issue needs Aaron before anyone continues. Filing it does not grant the request."
            )
        lines.append("")
        lines.append("GitHub does not block the issue on this comment. Edit the issue to update the record.")
    lines.append("")
    lines.append("This comment is the issue-hygiene workflow. It is not an agent claim.")
    return "\n".join(lines) + "\n"


def holder_is_live(agent: str | None, expires: datetime | None, at: datetime) -> bool:
    return bool(agent) and expires is not None and expires > at


def current_lease_conflicts(conflicts: Sequence[str]) -> tuple[str, ...]:
    """Overlap and foreign-release notes that still apply to the live holder."""
    return tuple(item for item in conflicts if " holds the lease" in item)


def without_previous_holder_conflicts(conflicts: list[str]) -> list[str]:
    """Drop warnings that named a holder this claim is no longer continuing."""
    return [item for item in conflicts if " holds the lease" not in item]


def assess_lease(comments: Sequence[Mapping[str, object]], now: datetime) -> LeaseState:
    """The earliest live claim holds. A later agent does not take an unexpired lease.

    Renewal by the same agent updates that holder's expiry and keeps the
    accepted claim's id and its warnings. After the holder releases, or after
    that lease ends, the next valid claim starts a new hold with a new id and
    does not inherit those warnings. Comment order is GitHub's receipt order.
    It is not a lock.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    holder_agent: str | None = None
    holder_expires: datetime | None = None
    lease_id: str | None = None
    release_id = "none"
    conflicts: list[str] = []
    saw_invalid = False
    saw_too_long = False
    saw_release = False

    for index, comment in enumerate(comments):
        created = try_parse_time(str(comment.get("created_at") or ""))
        comment_id = str(comment.get("id", index))
        if created is None:
            saw_invalid = True
            conflicts.append("ignored comment with an invalid timestamp")
            continue
        body = str(comment.get("body") or "")
        # An explicit Claim-Agent applies only inside this comment. A renewal
        # that omits one belongs to the accepted holder, not to a rejected claim.
        declared: str | None = None
        for raw in body.splitlines():
            line = raw.strip()
            claim = CLAIM_AGENT_RE.match(line)
            if claim:
                declared = claim.group(1).lower()
                continue
            release = RELEASE_RE.match(line)
            if release:
                who = release.group(1).lower()
                if holder_is_live(holder_agent, holder_expires, created) and who != holder_agent:
                    conflicts.append(f"ignored release by {who} while {holder_agent} holds the lease")
                    continue
                holder_agent = None
                holder_expires = None
                lease_id = None
                release_id = comment_id
                saw_release = True
                conflicts = without_previous_holder_conflicts(conflicts)
                continue
            expires = EXPIRES_RE.match(line)
            if not expires:
                continue
            exp = try_parse_time(expires.group(1))
            if exp is None:
                saw_invalid = True
                conflicts.append("ignored lease with an invalid timestamp")
                continue
            if declared is not None:
                agent = declared
            elif holder_agent is not None:
                agent = holder_agent
            else:
                agent = None
            if agent not in ALLOWED_AGENTS:
                saw_invalid = True
                conflicts.append("ignored lease with no known Claim-Agent")
                continue
            # A different agent does not take a live lease, even with a term
            # longer than 24 hours. Length is checked only for a claim that
            # would otherwise become the hold.
            if holder_is_live(holder_agent, holder_expires, created) and agent != holder_agent:
                conflicts.append(
                    f"ignored overlapping claim by {agent} while {holder_agent} holds the lease"
                )
                continue
            if exp - created > MAX_LEASE:
                saw_too_long = True
                conflicts.append(f"ignored lease by {agent} longer than 24 hours")
                continue
            # A same-agent renewal of an unexpired lease keeps that claim's id
            # and its warnings. Any other accepted claim is a new hold.
            live_renewal = holder_is_live(holder_agent, holder_expires, created) and agent == holder_agent
            if not live_renewal:
                conflicts = without_previous_holder_conflicts(conflicts)
                lease_id = comment_id
            holder_agent = agent
            holder_expires = exp
            saw_release = False

    recorded = tuple(conflicts)
    if holder_agent and holder_expires is not None:
        stamp = holder_expires.strftime("%Y-%m-%dT%H:%M:%SZ")
        suffix = f":c{len(recorded)}" if recorded else ""
        identity = lease_id or "none"
        if holder_expires <= now:
            marker = f"expired:{identity}:{stamp}{suffix}"
            return LeaseState("expired", holder_agent, holder_expires, marker, recorded, lease_id)
        marker = f"active:{identity}:{stamp}{suffix}"
        return LeaseState("active", holder_agent, holder_expires, marker, recorded, lease_id)
    if saw_release:
        return LeaseState("released", None, None, f"released:{release_id}", recorded)
    if saw_invalid and not saw_too_long:
        return LeaseState("invalid_agent", None, None, "invalid:none", recorded)
    if saw_too_long:
        return LeaseState("too_long", None, None, "too_long:none", recorded)
    return LeaseState("missing", None, None, "missing:none", recorded)


def lease_label_plan(labels: Sequence[str], state: LeaseState) -> LabelPlan | None:
    if state.status == "active":
        return None
    current = set(labels)
    if ACTIVE_LEASE_LABELS.isdisjoint(current):
        return None

    add: list[str] = []
    if "status:done" in current:
        remove = sorted(ACTIVE_LEASE_LABELS & current)
    elif "status:needs-owner" in current:
        remove = sorted(ACTIVE_LEASE_LABELS & current)
        if "lease:expired" not in current:
            add.append("lease:expired")
    else:
        remove = sorted(set(STATUS_LABELS) & current)
        add.append("status:ready")
        if "lease:expired" not in current:
            add.append("lease:expired")
    return LabelPlan(tuple(add), tuple(remove), state.marker, state.status)


def lease_comment(state: LeaseState) -> str:
    reasons = {
        "expired": "The holding lease is past Lease-Expires.",
        "released": "The holder released the lease, and the issue was still marked claimed or in progress.",
        "missing": "There is no Lease-Expires line.",
        "too_long": "The lease is longer than 24 hours, so it does not count.",
        "invalid_agent": (
            "The lease has no known Claim-Agent. "
            "Use chatgpt, grokbot, cursor, codex, or aaron."
        ),
    }
    lines = [
        f"<!-- egregore-lease:{state.marker} -->",
        "",
        "Lease backstop. This issue was still labeled claimed or in progress after the lease ended.",
        "",
        reasons[state.status],
    ]
    if state.agent:
        lines.append(f"Last Claim-Agent: {state.agent}")
    if state.expires is not None:
        lines.append(f"Recorded expiry: {state.expires.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.extend(
        [
            "",
            "Any agent may reclaim it, including the previous agent. Read the last progress note and continue from there.",
            "Do not wait for this workflow, and do not start a second copy of work that already has a branch or pull request.",
            "This workflow does not claim the issue.",
        ]
    )
    return "\n".join(lines) + "\n"


def lease_conflict_marker(state: LeaseState) -> str:
    """Dedup key for one hold. Renewals keep the claim id; a later hold does not."""
    relevant = current_lease_conflicts(state.conflicts)
    identity = state.lease_id or "none"
    return (
        "<!-- egregore-lease-conflict:"
        + identity
        + ":"
        + (state.agent or "")
        + ":"
        + " | ".join(relevant)
        + " -->"
    )


def conflict_comment_needed(comments: Sequence[Mapping[str, object]], state: LeaseState) -> bool:
    """True when this hold's overlap is not already noted in the thread."""
    if state.status != "active" or not current_lease_conflicts(state.conflicts):
        return False
    marker = lease_conflict_marker(state)
    return not any(marker in str(comment.get("body") or "") for comment in comments)


def lease_conflict_comment(state: LeaseState) -> str:
    stamp = state.expires.strftime("%Y-%m-%dT%H:%M:%SZ") if state.expires else "unknown"
    lines = [
        lease_conflict_marker(state),
        "",
        f"Overlapping claim ignored. {state.agent} still holds the lease until {stamp}.",
        "GitHub comments are not a lock. A later claim does not replace an earlier live lease.",
        "The later agent stops. Do not open a second branch or pull request for this task.",
    ]
    for item in current_lease_conflicts(state.conflicts):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("This workflow does not claim the issue.")
    return "\n".join(lines) + "\n"


def decision_verdict(body: str) -> str | None:
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        match = DECISION_RE.match(stripped)
        return match.group(1).lower() if match else None
    return None


def decision_plan(
    author: str,
    verdict: str | None,
    title: str,
    labels: Sequence[str],
) -> DecisionPlan | None:
    if verdict is None:
        return None
    # labels and title are accepted so callers can pass issue context. A comment
    # never moves status: a login is attribution, not a verified authorization.
    del title, labels
    if author != OWNER_LOGIN:
        return DecisionPlan(
            True,
            (),
            (),
            "This comment does not record an owner decision. The workflow did not change labels. "
            "A GitHub login is not a verified authorization.",
            "none",
        )
    return DecisionPlan(
        False,
        (),
        (),
        "Recorded decision only. GitHub attributes this comment to @AStrasler. "
        "That login is not proof Aaron personally approved, because an agent can post through his connected account. "
        "This is not a verified authorization. The workflow did not change labels, access, or merge state. "
        "Aaron verifies an owner action by doing it himself: merging, changing the setting, or moving the status label in a session he is driving.",
        "recorded",
    )


def render_decision_comment(plan: DecisionPlan, comment_id: str, verdict: str) -> str:
    lines = [f"<!-- egregore-decision:{plan.authorization}:{verdict}:{comment_id} -->", "", plan.note]
    lines.append("")
    lines.append("Authorization: recorded" if plan.authorization == "recorded" else "Authorization: none")
    return "\n".join(lines) + "\n"


def is_pull_request(issue: Mapping[str, object]) -> bool:
    return "pull_request" in issue


def label_names(labels: Sequence[object]) -> list[str]:
    names: list[str] = []
    for label in labels:
        if isinstance(label, dict):
            names.append(str(label.get("name", "")))
        else:
            names.append(str(label))
    return names


def repo_name() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not REPO_RE.fullmatch(repo):
        raise SystemExit("GITHUB_REPOSITORY must look like owner/name")
    return repo


def issue_number() -> int:
    raw = os.environ.get("ISSUE_NUMBER", "").strip()
    if not raw.isdigit():
        raise SystemExit("ISSUE_NUMBER is required")
    return int(raw)


def parse_gh_payload(text: str) -> object:
    """Decode one JSON value, or several pages concatenated by `gh api --paginate`.

    Each page is its own JSON array. A single `json.loads` call stops at the
    first page. List pages are flattened into the one list callers expect.
    """
    decoder = json.JSONDecoder()
    index = 0
    length = len(text)
    values: list[object] = []
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        value, index = decoder.raw_decode(text, index)
        values.append(value)
    if not values:
        raise json.JSONDecodeError("no JSON value", text, 0)
    if len(values) == 1:
        return values[0]
    if all(isinstance(value, list) for value in values):
        flat: list[object] = []
        for value in values:
            flat.extend(value)  # type: ignore[arg-type]
        return flat
    raise json.JSONDecodeError("paginated response was not a list of arrays", text, 0)


def gh_json(args: list[str], payload: Mapping[str, object] | None = None) -> object:
    command = ["gh", "api", *args]
    completed = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise GhError(redact(completed.stderr or completed.stdout or "gh api failed"))
    text = completed.stdout.strip()
    if not text:
        return None
    try:
        return parse_gh_payload(text)
    except json.JSONDecodeError:
        raise GhError("unexpected API response")


def apply_label_changes(repo: str, number: int, add: Sequence[str], remove: Sequence[str]) -> list[str]:
    warnings: list[str] = []
    for name in remove:
        encoded = quote(name, safe="")
        try:
            gh_json(["--method", "DELETE", f"repos/{repo}/issues/{number}/labels/{encoded}"])
        except GhError as err:
            if err.missing_resource or err.missing_label:
                continue
            raise
    if add:
        try:
            gh_json(
                ["--method", "POST", f"repos/{repo}/issues/{number}/labels", "--input", "-"],
                {"labels": list(add)},
            )
        except GhError as err:
            if err.missing_label:
                warnings.append("Label update skipped. Create labels with scripts/create-labels.sh.")
            else:
                raise
    return warnings


def upsert_marker_comment(repo: str, number: int, marker: str, body: str) -> None:
    comments = gh_json(
        ["--paginate", "--method", "GET", f"repos/{repo}/issues/{number}/comments", "-f", "per_page=100"]
    )
    if not isinstance(comments, list):
        raise GhError("unexpected comments payload")
    existing = next((comment for comment in comments if marker in str(comment.get("body") or "")), None)
    payload = {"body": body}
    if existing is None:
        gh_json(
            ["--method", "POST", f"repos/{repo}/issues/{number}/comments", "--input", "-"],
            payload,
        )
        return
    if str(existing.get("body") or "") == body:
        return
    comment_id = existing["id"]
    gh_json(
        ["--method", "PATCH", f"repos/{repo}/issues/comments/{comment_id}", "--input", "-"],
        payload,
    )


def sync_hygiene_labels(repo: str, number: int, labels: Sequence[object], check: IssueCheck) -> list[str]:
    names = set(label_names(list(labels)))
    add: list[str] = []
    remove: list[str] = []
    if check.needs_owner and "status:needs-owner" not in names:
        add.append("status:needs-owner")
    if check.needs_owner and "status:ready" in names:
        remove.append("status:ready")
    if not add and not remove:
        return []
    return apply_label_changes(repo, number, add, remove)


def cmd_check_issue(apply: bool, title: str | None, body_file: str | None) -> int:
    if apply:
        repo = repo_name()
        number = issue_number()
        issue = gh_json(["--method", "GET", f"repos/{repo}/issues/{number}"])
        if not isinstance(issue, dict):
            raise SystemExit("unexpected issue payload")
        check = validate_issue(str(issue.get("title") or ""), str(issue.get("body") or ""))
        should_note = (not check.ok) or check.needs_owner
        existing = gh_json(
            ["--paginate", "--method", "GET", f"repos/{repo}/issues/{number}/comments", "-f", "per_page=100"]
        )
        has_marker = isinstance(existing, list) and any(
            HYGIENE_MARKER in str(comment.get("body") or "") for comment in existing
        )
        if should_note or has_marker:
            upsert_marker_comment(
                repo,
                number,
                HYGIENE_MARKER,
                render_hygiene_comment(check, passed=check.ok and not check.needs_owner),
            )
        warnings = sync_hygiene_labels(repo, number, issue.get("labels") or [], check)
        for warning in warnings:
            print(warning, file=sys.stderr)
        return 0

    if body_file:
        body = Path(body_file).read_text(encoding="utf-8")
    else:
        body = sys.stdin.read()
    check = validate_issue(title or "", body)
    json.dump(check.to_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if check.ok else 1


def lease_watch_summary_line(
    *,
    scanned: int,
    active: int,
    expired: int,
    conflicts_warned: int,
) -> str:
    """One audit line for every lease-watch run, including an all-active run."""
    return (
        "lease-watch: "
        f"issues scanned {scanned}, "
        f"active {active}, "
        f"expired {expired}, "
        f"conflicts warned {conflicts_warned}"
    )


def cmd_check_leases(apply: bool, now_value: str | None) -> int:
    if not apply:
        print("check-leases only runs with --apply", file=sys.stderr)
        return 2
    now = parse_time(now_value) if now_value else datetime.now(timezone.utc)
    repo = repo_name()
    issues = gh_json(
        ["--paginate", "--method", "GET", f"repos/{repo}/issues", "-f", "state=open", "-f", "per_page=100"]
    )
    if not isinstance(issues, list):
        raise SystemExit("unexpected issues payload")
    failed = False
    scanned = 0
    active = 0
    expired = 0
    conflicts_warned = 0
    for issue in issues:
        if not isinstance(issue, dict) or is_pull_request(issue):
            continue
        number = issue.get("number")
        scanned += 1
        try:
            labels = label_names(issue.get("labels") or [])
            comments = gh_json(
                [
                    "--paginate",
                    "--method",
                    "GET",
                    f"repos/{repo}/issues/{number}/comments",
                    "-f",
                    "per_page=100",
                ]
            )
            if not isinstance(comments, list):
                raise GhError("unexpected comments payload")
            state = assess_lease(comments, now)
            if state.status == "active":
                active += 1
            elif state.status == "expired":
                expired += 1
            if conflict_comment_needed(comments, state):
                gh_json(
                    ["--method", "POST", f"repos/{repo}/issues/{number}/comments", "--input", "-"],
                    {"body": lease_conflict_comment(state)},
                )
                conflicts_warned += 1
            plan = lease_label_plan(labels, state)
            if plan is None:
                continue
            marker = f"<!-- egregore-lease:{plan.marker} -->"
            if not any(marker in str(comment.get("body") or "") for comment in comments):
                gh_json(
                    ["--method", "POST", f"repos/{repo}/issues/{number}/comments", "--input", "-"],
                    {"body": lease_comment(state)},
                )
            if plan.add or plan.remove:
                warnings = apply_label_changes(repo, int(number), plan.add, plan.remove)
                for warning in warnings:
                    print(f"issue {number}: {warning}", file=sys.stderr)
            print(f"issue {number}: {state.status}", file=sys.stderr)
        except (GhError, ValueError) as err:
            failed = True
            message = err.message if isinstance(err, GhError) else "invalid lease data"
            print(f"issue {number}: {message}", file=sys.stderr)
    print(
        lease_watch_summary_line(
            scanned=scanned,
            active=active,
            expired=expired,
            conflicts_warned=conflicts_warned,
        ),
        file=sys.stderr,
    )
    return 1 if failed else 0


def cmd_check_decision(apply: bool) -> int:
    if not apply:
        print("check-decision only runs with --apply", file=sys.stderr)
        return 2
    repo = repo_name()
    number = issue_number()
    comment_id = os.environ.get("COMMENT_ID", "").strip()
    if not comment_id.isdigit():
        raise SystemExit("COMMENT_ID is required")
    issue = gh_json(["--method", "GET", f"repos/{repo}/issues/{number}"])
    if not isinstance(issue, dict):
        raise SystemExit("unexpected issue payload")
    if is_pull_request(issue):
        return 0
    comment = gh_json(["--method", "GET", f"repos/{repo}/issues/comments/{comment_id}"])
    if not isinstance(comment, dict):
        raise SystemExit("unexpected comment payload")
    verdict = decision_verdict(str(comment.get("body") or ""))
    if verdict is None:
        return 0
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    author = str(user.get("login") or "")
    plan = decision_plan(author, verdict, str(issue.get("title") or ""), label_names(issue.get("labels") or []))
    if plan is None:
        return 0
    body = render_decision_comment(plan, comment_id, verdict)
    marker = body.splitlines()[0]
    comments = gh_json(
        ["--paginate", "--method", "GET", f"repos/{repo}/issues/{number}/comments", "-f", "per_page=100"]
    )
    if not isinstance(comments, list):
        raise SystemExit("unexpected comments payload")
    if not any(marker in str(item.get("body") or "") for item in comments):
        gh_json(
            ["--method", "POST", f"repos/{repo}/issues/{number}/comments", "--input", "-"],
            {"body": body},
        )
    if plan.add or plan.remove:
        warnings = apply_label_changes(repo, number, plan.add, plan.remove)
        for warning in warnings:
            print(warning, file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coordination")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-issue")
    check.add_argument("--apply", action="store_true")
    check.add_argument("--title")
    check.add_argument("--body-file")

    leases = sub.add_parser("check-leases")
    leases.add_argument("--apply", action="store_true")
    leases.add_argument("--now")

    decision = sub.add_parser("check-decision")
    decision.add_argument("--apply", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "check-issue":
            return cmd_check_issue(args.apply, args.title, args.body_file)
        if args.command == "check-leases":
            return cmd_check_leases(args.apply, args.now)
        return cmd_check_decision(args.apply)
    except GhError as err:
        print(err.message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
