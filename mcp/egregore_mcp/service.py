"""Coordination tools. Lease and decision rules come from scripts/coordination.py.

Nothing here merges, deploys, changes repository settings, or records an owner
decision. A shared GitHub token still shows up as that token's login.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import coordination as coord  # noqa: E402

from egregore_mcp.comments import (  # noqa: E402
    REVIEW_COMMENT,
    claim_comment,
    conflict_comment,
    handoff_issue_body,
    handoff_note,
    progress_comment,
    release_comment,
    renewal_comment,
    work_repository,
)
from egregore_mcp.github_api import GitHubError, RestGitHub  # noqa: E402

MAX_REVIEW_ROUNDS = 3
TEXT_LIMIT = 4000
RELEASE_STATUSES = frozenset(
    {"status:ready", "status:blocked", "status:needs-review", "status:needs-owner", "status:done"}
)
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_STATE_PREFIX = "codex-review-state:v1 "
_RECEIPT_RE = re.compile(r"Reviewed commit:\*\*\s*`([0-9a-fA-F]{7,40})`")
_PULL_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)
_NO_PULL_RE = re.compile(
    r"\b(no pull request|without a pull request|no merge|documentation only|docs only|research only)\b",
    re.IGNORECASE,
)
_WANTS_PULL_RE = re.compile(r"\bpull request\b", re.IGNORECASE)
_ISSUE_REF_RE = re.compile(r"(?<![\w/])#(\d+)\b")
_CODEX_BOT = "chatgpt-codex-connector"


def stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ok(result: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "result": result, "error": None}


def err(code: str, message: str, **details: object) -> dict[str, object]:
    return {"ok": False, "result": None, "error": {"code": code, "message": message, "details": dict(details)}}


def redact_text(text: str) -> str:
    cleaned = text
    for pattern in coord.SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned


def _same_commit(left: str, right: str) -> bool:
    shorter, longer = sorted((left.lower(), right.lower()), key=len)
    return bool(shorter) and longer.startswith(shorter)


def _review_state(body: str) -> dict[str, object] | None:
    index = body.find(_STATE_PREFIX)
    if index == -1:
        return None
    start = body.find("{", index)
    if start == -1:
        return None
    try:
        value, _end = json.JSONDecoder().raw_decode(body[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def completed_reviews(comments: Sequence[Mapping[str, object]]) -> tuple[list[dict[str, object]], str | None]:
    """Distinct completed Codex reviews, oldest first, plus a pending status.

    A summary comment and a receipt for the same commit are one round. The
    summary's has_unresolved_findings flag wins when both name that commit.
    """
    rounds: list[dict[str, object]] = []
    pending: str | None = None
    last_request = ""
    last_result = ""
    for comment in comments:
        body = str(comment.get("body") or "")
        # Summary comments are edited in place, so updated_at is the completion time.
        # A review request uses created_at. Editing the summary must not look older
        # than the request that it already answered.
        result_at = str(comment.get("updated_at") or comment.get("created_at") or "")
        request_at = str(comment.get("created_at") or "")
        state = _review_state(body)
        if state is not None:
            code_review = state.get("code_review")
            if isinstance(code_review, dict):
                status = code_review.get("status")
                sha = str(code_review.get("commit_sha") or "")
                if status == "completed" and sha:
                    rounds.append(
                        {
                            "sha": sha,
                            "unresolved": bool(code_review.get("has_unresolved_findings")),
                            "comment_id": str(comment.get("id", "")),
                            "source": "summary",
                        }
                    )
                    if result_at >= last_result:
                        last_result = result_at
                elif status not in (None, "", "completed"):
                    pending = str(status)
        if "codex-review-receipt" in body:
            found = _RECEIPT_RE.search(body)
            if found:
                unresolved: bool | None = None
                if "Didn't find any major issues" in body:
                    unresolved = False
                rounds.append(
                    {
                        "sha": found.group(1),
                        "unresolved": unresolved,
                        "comment_id": str(comment.get("id", "")),
                        "source": "receipt",
                    }
                )
            if result_at >= last_result:
                last_result = result_at
        if body.strip() == REVIEW_COMMENT and request_at >= last_request:
            last_request = request_at
    if pending is None and last_request and last_request > last_result:
        pending = "requested"
    return _merge_rounds(rounds), pending


def _merged_unresolved(existing: Mapping[str, object], item: Mapping[str, object]) -> object:
    """The summary flag wins over a receipt for the same commit, in either order."""
    if existing.get("source") == "summary" and item.get("source") != "summary":
        return existing["unresolved"]
    if item.get("source") == "summary" and existing.get("source") != "summary":
        return item["unresolved"]
    if item["unresolved"] is not None:
        return item["unresolved"]
    return existing["unresolved"]


def _merge_rounds(rounds: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for item in rounds:
        sha = str(item["sha"])
        for index, existing in enumerate(merged):
            if not _same_commit(str(existing["sha"]), sha):
                continue
            unresolved = _merged_unresolved(existing, item)
            longer = sha if len(sha) >= len(str(existing["sha"])) else str(existing["sha"])
            source = "summary" if "summary" in (existing.get("source"), item.get("source")) else item.get("source")
            merged[index] = {
                "sha": longer,
                "unresolved": unresolved,
                "comment_id": existing["comment_id"],
                "source": source,
            }
            break
        else:
            merged.append(dict(item))
    return merged


def review_block(comments: Sequence[Mapping[str, object]], head: str) -> dict[str, object] | None:
    rounds, pending = completed_reviews(comments)
    base = {"completed_rounds": len(rounds), "round_limit": MAX_REVIEW_ROUNDS, "head_sha": head}
    if pending:
        return err(
            "review_pending",
            "A Codex review is still running for this pull request. Wait for the receipt before asking again.",
            status=pending,
            **base,
        )
    matched = [item for item in rounds if _same_commit(str(item["sha"]), head)]
    if matched and matched[-1]["unresolved"] is False:
        return err(
            "review_current",
            "The review receipt already names this head and reports no unresolved findings.",
            reviewed_sha=matched[-1]["sha"],
            **base,
        )
    if matched and matched[-1]["unresolved"] is True:
        return err(
            "review_same_head",
            "Codex already reviewed this head and left findings. Push a new commit before requesting another review.",
            reviewed_sha=matched[-1]["sha"],
            **base,
        )
    if matched:
        return err(
            "review_duplicate",
            "A Codex review already covers this head commit. Push a new commit before requesting another review.",
            reviewed_sha=matched[-1]["sha"],
            **base,
        )
    if len(rounds) >= MAX_REVIEW_ROUNDS:
        return err(
            "review_limit",
            "Three Codex reviews are the limit. Open one owner-decision issue instead of a fourth request.",
            **base,
        )
    return None


def lease_view(state: coord.LeaseState) -> dict[str, object]:
    return {
        "status": state.status,
        "agent": state.agent,
        "expires": stamp(state.expires) if state.expires is not None else None,
        "lease_id": state.lease_id,
        "marker": state.marker,
        "conflicts": list(state.conflicts),
    }


def stall_view(labels: Sequence[str], state: coord.LeaseState) -> dict[str, object]:
    """Read-only mirror of the lease-watch stall gate.

    ``stall_plan`` skips an issue while its lease is active, and ``stall_reason``
    is empty unless the labels are needs-review, a handoff, or needs-owner
    (and not done). This server does not post the stall comment or add
    ``stall:flagged``. Lease watch owns that write.
    """
    return {
        "reason": coord.stall_reason(labels),
        "flagged": coord.STALL_LABEL in set(labels),
        "lease_blocks_flag": state.status == "active",
    }


def _mentions_issue(body: str, number: int) -> bool:
    if any(int(match.group(1)) == number for match in _ISSUE_REF_RE.finditer(body)):
        return True
    return bool(re.search(rf"/issues/{number}\b", body))


def _author(comment: Mapping[str, object]) -> str:
    user = comment.get("user")
    if isinstance(user, dict):
        return str(user.get("login") or "")
    return ""


def _classify(body: str) -> str:
    lines = [line.strip() for line in body.replace("\r\n", "\n").split("\n")]
    if any("egregore-lease-conflict:" in line for line in lines):
        return "lease_conflict"
    if any(line.startswith("<!-- egregore-lease:") for line in lines):
        return "lease_backstop"
    if any("<!-- egregore-stall:" in line for line in lines):
        return "stall"
    if any(coord.HYGIENE_MARKER in line for line in lines):
        return "hygiene"
    if any(line.startswith("<!-- egregore-decision:") for line in lines):
        return "decision_note"
    if coord.decision_verdict(body):
        return "decision"
    # A handoff comment also carries Lease-Release. The heading is the event.
    if any(line == "## Handoff" for line in lines):
        return "handoff"
    if any(coord.RELEASE_RE.match(line) for line in lines):
        return "release"
    has_expiry = any(coord.EXPIRES_RE.match(line) for line in lines)
    if has_expiry and any(line == "## Lease renewal" for line in lines):
        return "renewal"
    if has_expiry and any(coord.CLAIM_AGENT_RE.match(line) for line in lines):
        return "claim"
    if has_expiry:
        return "renewal"
    if any(line == "## Progress" for line in lines):
        return "progress"
    if any(line == "## Evidence" for line in lines):
        return "evidence"
    return "comment"


def _agent_line(body: str) -> str | None:
    for line in body.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("agent:"):
            return stripped.split(":", 1)[1].strip() or None
    return None


class CoordinationService:
    def __init__(
        self,
        github: RestGitHub | None,
        agent: str,
        *,
        repo: str = "AStrasler/Egeregore",
        now: Callable[[], datetime] | None = None,
        config_problems: Sequence[str] = (),
    ) -> None:
        self.github = github
        self.agent = agent
        self.repo = repo
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.config_problems = tuple(config_problems)

    def get_tasks(
        self,
        *,
        state: str = "open",
        kind: str | None = "task",
        status: str | None = None,
        project: str | None = None,
        role: str | None = None,
        limit: int = 30,
    ) -> dict[str, object]:
        blocked = self._ready()
        if blocked:
            return blocked
        if state not in {"open", "closed", "all"}:
            return err("invalid_input", "state must be open, closed, or all.")
        if kind not in {None, "", "all", "task", "handoff", "evidence", "review", "decision"}:
            return err("invalid_input", "kind must be task, handoff, evidence, review, decision, or all.")
        limit_error = self._limit(limit)
        if limit_error:
            return limit_error
        try:
            issues = self.github.list_issues(state=state)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        rows: list[dict[str, object]] = []
        for issue in issues:
            if coord.is_pull_request(issue):
                continue
            if not self._matches(issue, kind=kind, status=status, project=project, role=role):
                continue
            number = int(issue["number"])
            try:
                comments = self.github.list_comments(number)  # type: ignore[union-attr]
            except GitHubError as exc:
                return self._github_err(exc)
            lease = coord.assess_lease(comments, self._aware_now())
            rows.append(self._summary(issue, lease))
            if len(rows) >= limit:
                break
        return ok({"tasks": rows, "count": len(rows)})

    def get_task(self, issue_number: int) -> dict[str, object]:
        loaded = self._load(issue_number)
        if isinstance(loaded, dict) and loaded.get("ok") is False:
            return loaded
        issue, comments = loaded  # type: ignore[misc]
        lease = coord.assess_lease(comments, self._aware_now())
        body = redact_text(str(issue.get("body") or ""))
        return ok(
            {
                "issue": self._summary(issue, lease),
                "body": body,
                "sections": {name: redact_text(value) for name, value in coord.sections(body).items()},
                "lease": lease_view(lease),
                "identity": self._identity(),
            }
        )

    def claim_task(self, issue_number: int, lease_hours: int = 6) -> dict[str, object]:
        loaded = self._load(issue_number)
        if isinstance(loaded, dict) and loaded.get("ok") is False:
            return loaded
        issue, comments = loaded  # type: ignore[misc]
        hours = self._hours(lease_hours)
        if isinstance(hours, dict):
            return hours
        state = coord.assess_lease(comments, self._aware_now())
        refusal = self._claim_refusal(issue, state)
        if refusal:
            return refusal
        if state.status == "active" and state.agent == self.agent:
            return ok(
                {
                    "issue_number": issue_number,
                    "held": True,
                    "already_held": True,
                    "lease": lease_view(state),
                    "identity": self._identity(),
                }
            )
        if state.status == "active" and state.agent != self.agent:
            return self._losing_claim(issue_number, issue, hours, state.agent)
        return self._winning_claim(issue_number, issue, hours)

    def renew_lease(self, issue_number: int, lease_hours: int = 6, note: str = "") -> dict[str, object]:
        loaded = self._holder(issue_number)
        if isinstance(loaded, dict) and loaded.get("ok") is False:
            return loaded
        _issue, state = loaded  # type: ignore[misc]
        hours = self._hours(lease_hours)
        if isinstance(hours, dict):
            return hours
        note_error = self._optional_text(note, "note")
        if note_error:
            return note_error
        if state.status != "active" or state.agent != self.agent:
            return self._not_holding(state)
        fresh = self._guard_mutation(issue_number)
        if isinstance(fresh, dict) and fresh.get("ok") is False:
            return fresh
        expires = stamp(self._expiry(hours))
        body = renewal_comment(agent=self.agent, expires=expires, note=note)
        posted = self._comment(issue_number, body)
        if isinstance(posted, dict) and posted.get("ok") is False:
            return posted
        fresh = self._assess(issue_number)
        if isinstance(fresh, dict) and fresh.get("ok") is False:
            return fresh
        return ok(
            {
                "issue_number": issue_number,
                "comment_id": str(posted["id"]),  # type: ignore[index]
                "lease": lease_view(fresh),
                "identity": self._identity(),
            }
        )

    def release_task(self, issue_number: int, status: str, reason: str) -> dict[str, object]:
        loaded = self._load(issue_number)
        if isinstance(loaded, dict) and loaded.get("ok") is False:
            return loaded
        issue, comments = loaded  # type: ignore[misc]
        label = status if status.startswith("status:") else f"status:{status}"
        if label not in RELEASE_STATUSES:
            return err("invalid_input", "status must be ready, blocked, needs-review, needs-owner, or done.")
        reason_error = self._required_text(reason, "reason")
        if reason_error:
            return reason_error
        labels = coord.label_names(issue.get("labels") or [])
        if self._needs_owner(issue) and "status:needs-owner" not in labels:
            return err("not_claimable", "This issue is waiting on the owner. release_task did not post.")
        state = coord.assess_lease(comments, self._aware_now())
        holder = state.status == "active" and state.agent == self.agent
        last_holder = state.status == "expired" and state.agent == self.agent
        if not holder and not last_holder:
            return self._not_holding(state)
        fresh = self._guard_mutation(issue_number)
        if isinstance(fresh, dict) and fresh.get("ok") is False:
            return fresh
        if label == "status:done":
            done_refusal = self._done_refusal(fresh, comments)
            if done_refusal:
                return done_refusal
            fresh = self._guard_mutation(issue_number)
            if isinstance(fresh, dict) and fresh.get("ok") is False:
                return fresh
        body = release_comment(agent=self.agent, reason=reason)
        posted = self._comment(issue_number, body)
        if isinstance(posted, dict) and posted.get("ok") is False:
            return posted
        current = self._guard_mutation(issue_number)
        if isinstance(current, dict) and current.get("ok") is False:
            return current
        labeled = self._set_status(issue_number, current, label, agent=self.agent, drop_expired=True)
        if isinstance(labeled, dict) and labeled.get("ok") is False:
            return labeled
        fresh = self._assess(issue_number)
        if isinstance(fresh, dict) and fresh.get("ok") is False:
            return fresh
        return ok(
            {
                "issue_number": issue_number,
                "comment_id": str(posted["id"]),  # type: ignore[index]
                "status": label,
                "lease": lease_view(fresh),
                "identity": self._identity(),
            }
        )

    def post_progress(self, issue_number: int, done: str, next_step: str) -> dict[str, object]:
        loaded = self._holder(issue_number)
        if isinstance(loaded, dict) and loaded.get("ok") is False:
            return loaded
        _issue, _state = loaded  # type: ignore[misc]
        for field, value in (("done", done), ("next", next_step)):
            text_error = self._required_text(value, field)
            if text_error:
                return text_error
        fresh = self._guard_mutation(issue_number)
        if isinstance(fresh, dict) and fresh.get("ok") is False:
            return fresh
        body = progress_comment(agent=self.agent, done=done, next_step=next_step)
        posted = self._comment(issue_number, body)
        if isinstance(posted, dict) and posted.get("ok") is False:
            return posted
        current = self._guard_mutation(issue_number)
        if isinstance(current, dict) and current.get("ok") is False:
            return current
        labels = coord.label_names(current.get("labels") or [])
        if "status:claimed" in labels and "status:needs-owner" not in labels:
            labeled = self._set_status(issue_number, current, "status:in-progress", agent=self.agent, drop_expired=True)
            if isinstance(labeled, dict) and labeled.get("ok") is False:
                return labeled
        return ok(
            {
                "issue_number": issue_number,
                "comment_id": str(posted["id"]),  # type: ignore[index]
                "identity": self._identity(),
            }
        )

    def handoff_task(
        self,
        issue_number: int,
        to_agent: str,
        done_so_far: str,
        next_action: str,
        title: str = "",
    ) -> dict[str, object]:
        loaded = self._holder(issue_number)
        if isinstance(loaded, dict) and loaded.get("ok") is False:
            return loaded
        issue, _state = loaded  # type: ignore[misc]
        target = to_agent.strip().lower()
        if target not in coord.ALLOWED_AGENTS:
            return err("invalid_input", "to_agent must be chatgpt, grokbot, cursor, codex, or aaron.")
        if target == self.agent:
            return err("invalid_input", "A handoff goes to a different role. Keep working on this issue instead.")
        for field, value in (("done_so_far", done_so_far), ("next_action", next_action)):
            text_error = self._required_text(value, field)
            if text_error:
                return text_error
        title_error = self._optional_text(title, "title")
        if title_error:
            return title_error
        fresh = self._guard_mutation(issue_number)
        if isinstance(fresh, dict) and fresh.get("ok") is False:
            return fresh
        issue = fresh
        parsed = coord.sections(str(issue.get("body") or ""))
        project = coord.first_line(parsed.get("Project")) or "coordination"
        project_slug = coord.first_line(parsed.get("Project slug"))
        if project_slug == "_No response_":
            project_slug = ""
        body = handoff_issue_body(
            project=project,
            project_slug=project_slug,
            source_agent=self.agent,
            to_agent=target,
            issue_number=issue_number,
            done_so_far=done_so_far,
            next_action=next_action,
            repo=work_repository(project, project_slug, str(issue.get("body") or "")),
            boundary=coord.BOUNDARY_SENTENCE,
        )
        handoff_title = f"[Handoff]: {(title.strip() or str(issue.get('title') or 'next step'))[:120]}"
        check = coord.validate_issue(handoff_title, body)
        if not check.ok:
            return err("invalid_input", "The handoff text did not pass the coordination field check.", problems=list(check.problems))
        labels = ["type:handoff", "status:ready", f"agent:{target}"]
        for name in coord.label_names(issue.get("labels") or []):
            if name.startswith("project:") or name.startswith("priority:"):
                labels.append(name)
        try:
            created = self.github.create_issue(title=handoff_title, body=body, labels=labels)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        number = int(created["number"])
        current = self._guard_mutation(issue_number)
        if isinstance(current, dict) and current.get("ok") is False:
            return current
        note = handoff_note(agent=self.agent, to_agent=target, handoff_number=number)
        posted = self._comment(issue_number, note)
        if isinstance(posted, dict) and posted.get("ok") is False:
            return posted
        current = self._guard_mutation(issue_number)
        if isinstance(current, dict) and current.get("ok") is False:
            return current
        labeled = self._set_status(issue_number, current, "status:blocked", agent=self.agent, drop_expired=True)
        if isinstance(labeled, dict) and labeled.get("ok") is False:
            return labeled
        return ok(
            {
                "issue_number": issue_number,
                "handoff_issue": number,
                "to_agent": target,
                "comment_id": str(posted["id"]),  # type: ignore[index]
                "identity": self._identity(),
            }
        )

    def request_review(self, pull_number: int) -> dict[str, object]:
        blocked = self._ready()
        if blocked:
            return blocked
        number = self._number(pull_number)
        if isinstance(number, dict):
            return number
        try:
            pull = self.github.get_pull(number)  # type: ignore[union-attr]
            comments = self.github.list_comments(number)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        head = ""
        head_obj = pull.get("head")
        if isinstance(head_obj, dict):
            head = str(head_obj.get("sha") or "")
        if not _SHA_RE.fullmatch(head):
            return err("invalid_input", "The pull request has no head SHA to review.")
        pull_state = str(pull.get("state") or "open").lower()
        if pull_state == "closed" or pull.get("merged") is True:
            return err(
                "not_reviewable",
                "This pull request is closed. request_review does not post.",
                state=pull_state,
                merged=pull.get("merged") is True,
            )
        refusal = review_block(comments, head)
        if refusal:
            return refusal
        posted = self._comment(number, REVIEW_COMMENT)
        if isinstance(posted, dict) and posted.get("ok") is False:
            return posted
        rounds, _pending = completed_reviews(comments)
        return ok(
            {
                "pull_number": number,
                "comment_id": str(posted["id"]),  # type: ignore[index]
                "body": REVIEW_COMMENT,
                "head_sha": head,
                "completed_rounds": len(rounds),
                "round_limit": MAX_REVIEW_ROUNDS,
                "identity": self._identity(),
            }
        )

    def get_owner_decisions(self, *, state: str = "open", limit: int = 30) -> dict[str, object]:
        blocked = self._ready()
        if blocked:
            return blocked
        if state not in {"open", "closed", "all"}:
            return err("invalid_input", "state must be open, closed, or all.")
        limit_error = self._limit(limit)
        if limit_error:
            return limit_error
        try:
            issues = self.github.list_issues(state=state)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        rows: list[dict[str, object]] = []
        for issue in issues:
            if coord.is_pull_request(issue):
                continue
            labels = coord.label_names(issue.get("labels") or [])
            kind = coord.kind_from_title(str(issue.get("title") or ""))
            if kind != "decision" and "type:decision" not in labels:
                continue
            number = int(issue["number"])
            try:
                comments = self.github.list_comments(number)  # type: ignore[union-attr]
            except GitHubError as exc:
                return self._github_err(exc)
            decisions: list[dict[str, object]] = []
            for comment in comments:
                verdict = coord.decision_verdict(str(comment.get("body") or ""))
                if verdict is None:
                    continue
                author = _author(comment)
                plan = coord.decision_plan(author, verdict, str(issue.get("title") or ""), labels)
                authorization = plan.authorization if plan is not None else "none"
                if authorization not in {"none", "recorded"}:
                    authorization = "none"
                decisions.append(
                    {
                        "comment_id": str(comment.get("id", "")),
                        "author": author,
                        "verdict": verdict,
                        "authorization": authorization,
                        "note": plan.note if plan is not None else "",
                    }
                )
            rows.append(
                {
                    "number": number,
                    "title": issue.get("title"),
                    "html_url": issue.get("html_url"),
                    "labels": labels,
                    "decisions": decisions,
                }
            )
            if len(rows) >= limit:
                break
        return ok(
            {
                "decisions": rows,
                "count": len(rows),
                "authorization_values": ["none", "recorded"],
                "identity": self._identity(),
            }
        )

    def get_task_history(self, issue_number: int) -> dict[str, object]:
        loaded = self._load(issue_number)
        if isinstance(loaded, dict) and loaded.get("ok") is False:
            return loaded
        issue, comments = loaded  # type: ignore[misc]
        events: list[dict[str, object]] = []
        for comment in comments:
            body = str(comment.get("body") or "")
            events.append(
                {
                    "comment_id": str(comment.get("id", "")),
                    "created_at": comment.get("created_at"),
                    "author": _author(comment),
                    "agent": _agent_line(body),
                    "kind": _classify(body),
                    "body": redact_text(body),
                }
            )
        lease = coord.assess_lease(comments, self._aware_now())
        return ok(
            {
                "issue_number": issue_number,
                "title": issue.get("title"),
                "events": events,
                "lease": lease_view(lease),
                "identity": self._identity(),
            }
        )

    def _ready(self) -> dict[str, object] | None:
        if self.config_problems or self.github is None:
            return err(
                "unconfigured",
                "Set EGREGORE_AGENT and EGREGORE_GITHUB_TOKEN before calling tools.",
                missing=list(self.config_problems) or ["EGREGORE_GITHUB_TOKEN"],
            )
        if self.agent not in coord.ALLOWED_AGENTS:
            return err("unconfigured", "EGREGORE_AGENT must be chatgpt, grokbot, cursor, codex, or aaron.")
        return None

    def _aware_now(self) -> datetime:
        current = self.now()
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(timezone.utc)

    def _identity(self) -> dict[str, object]:
        return {
            "agent": self.agent,
            "github_login_is_separate": False,
            "note": (
                "Comments are attributed to the GitHub user of EGREGORE_GITHUB_TOKEN. "
                "A shared token for @AStrasler is still @AStrasler. The Agent line is the role, not a login."
            ),
        }

    def _hours(self, value: object) -> int | dict[str, object]:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 24:
            return err("invalid_input", "lease_hours must be an integer from 1 to 24.")
        return value

    def _limit(self, value: object) -> dict[str, object] | None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            return err("invalid_input", "limit must be an integer from 1 to 100.")
        return None

    def _number(self, value: object) -> int | dict[str, object]:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return err("invalid_input", "The issue or pull request number must be a positive integer.")
        return value

    def _expiry(self, hours: int) -> datetime:
        span = timedelta(hours=hours)
        # One second under 24h so a comment timestamp slightly before this
        # clock still satisfies coordination.MAX_LEASE.
        if span >= coord.MAX_LEASE:
            span = coord.MAX_LEASE - timedelta(seconds=1)
        return self._aware_now() + span

    def _required_text(self, value: object, field: str) -> dict[str, object] | None:
        if not isinstance(value, str) or not value.strip() or value.strip() == "_No response_":
            return err("invalid_input", f"{field} is empty.")
        return self._screen(value, field)

    def _optional_text(self, value: object, field: str) -> dict[str, object] | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return err("invalid_input", f"{field} must be a string.")
        if not value.strip():
            return None
        return self._screen(value, field)

    def _screen(self, value: str, field: str) -> dict[str, object] | None:
        if len(value) > TEXT_LIMIT:
            return err("invalid_input", f"{field} is longer than {TEXT_LIMIT} characters.")
        if coord.looks_like_secret(value):
            return err("secret_rejected", f"{field} looks like a credential. It was not posted.")
        if coord.decision_verdict(value):
            return err("forbidden", "This server does not record or apply owner decisions.")
        return None

    def _load(self, issue_number: int) -> tuple[Mapping[str, object], list[dict[str, object]]] | dict[str, object]:
        blocked = self._ready()
        if blocked:
            return blocked
        number = self._number(issue_number)
        if isinstance(number, dict):
            return number
        try:
            issue = self.github.get_issue(number)  # type: ignore[union-attr]
            comments = self.github.list_comments(number)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        return issue, comments

    def _holder(self, issue_number: int) -> tuple[Mapping[str, object], coord.LeaseState] | dict[str, object]:
        loaded = self._load(issue_number)
        if isinstance(loaded, dict) and loaded.get("ok") is False:
            return loaded
        issue, comments = loaded  # type: ignore[misc]
        if self._needs_owner(issue):
            return err("not_claimable", "This issue is waiting on the owner.")
        state = coord.assess_lease(comments, self._aware_now())
        if state.status == "expired":
            return err(
                "lease_expired",
                "The lease has expired. claim_task starts a new one. This call did not post.",
                lease=lease_view(state),
            )
        if state.status != "active" or state.agent != self.agent:
            return self._not_holding(state)
        refusal = self._holder_state_refusal(issue)
        if refusal:
            return refusal
        return issue, state

    def _holder_state_refusal(self, issue: Mapping[str, object]) -> dict[str, object] | None:
        if str(issue.get("state") or "open").lower() == "closed":
            return err("not_claimable", "This issue is closed. The call did not post.")
        labels = coord.label_names(issue.get("labels") or [])
        if "status:done" in labels:
            return err("not_claimable", "This issue is already done. The call did not post.")
        if "status:blocked" in labels:
            return err(
                "not_claimable",
                "This issue is blocked. The call did not post. Move it back to status:ready after the unblocker happens.",
            )
        if self._needs_owner(issue):
            return err("not_claimable", "This issue is waiting on the owner. The call did not post.")
        return None

    def _guard_mutation(self, number: int) -> Mapping[str, object] | dict[str, object]:
        """Read the issue again immediately before a write."""
        try:
            issue = self.github.get_issue(number)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        refusal = self._holder_state_refusal(issue)
        if refusal:
            return refusal
        return issue

    def _claim_refusal(self, issue: Mapping[str, object], lease: coord.LeaseState) -> dict[str, object] | None:
        if coord.is_pull_request(issue):
            return err("invalid_input", "claim_task applies to issues, not pull requests.")
        if str(issue.get("state") or "open").lower() == "closed":
            return err("not_claimable", "This issue is closed. claim_task does not post.")
        labels = coord.label_names(issue.get("labels") or [])
        title = str(issue.get("title") or "")
        kind = coord.kind_from_title(title)
        is_task = "type:task" in labels and kind == "task"
        is_handoff = "type:handoff" in labels and kind == "handoff"
        if not is_task and not is_handoff:
            return err(
                "not_claimable",
                "claim_task only claims open type:task issues, or a handoff for the role named in To.",
            )
        if self._needs_owner(issue):
            return err("not_claimable", "This issue is waiting on the owner. claim_task does not override that.")
        if "status:done" in labels:
            return err("not_claimable", "This issue is already done.")
        if "status:blocked" in labels:
            return err(
                "not_claimable",
                "This issue is blocked. claim_task does not clear that. Move it back to status:ready after the unblocker happens.",
            )
        if "status:needs-review" in labels:
            return err(
                "not_claimable",
                "This issue is waiting on review. claim_task does not clear that.",
            )
        ready = "status:ready" in labels
        held = "status:claimed" in labels or "status:in-progress" in labels
        expired_hold = held and lease.status == "expired"
        live_hold = held and lease.status == "active"
        if not ready and not expired_hold and not live_hold:
            return err(
                "not_claimable",
                "claim_task only claims status:ready work, or claimed or in-progress work whose lease has expired.",
            )
        role = self._expected_role(issue)
        if role and role != "any" and role != self.agent:
            return err("wrong_role", f"This issue is for {role}.", expected_role=role, agent=self.agent)
        return None

    def _done_refusal(
        self,
        issue: Mapping[str, object],
        comments: Sequence[Mapping[str, object]],
    ) -> dict[str, object] | None:
        if self._needs_owner(issue):
            return err("not_claimable", "This issue is waiting on the owner. release_task will not set status:done.")
        number = int(issue["number"])
        evidence = self._has_completion_evidence(number, comments)
        if isinstance(evidence, dict):
            return evidence
        if not evidence:
            return err(
                "not_done",
                "status:done needs an evidence comment or an evidence issue that links this task. Nothing was posted.",
            )
        requirement = self._pull_requirement(issue, comments)
        if requirement == "waived":
            return None
        if requirement == "outside":
            return err(
                "not_done",
                "A linked pull request is outside this coordination repository. "
                "release_task cannot verify that merge and did not set status:done.",
            )
        if requirement != "required":
            return err(
                "not_done",
                "status:done needs a linked pull request in this repository, or text that this task needs no pull request. "
                "Nothing was posted.",
            )
        return self._pulls_are_complete(issue, comments)

    def _has_completion_evidence(
        self,
        number: int,
        comments: Sequence[Mapping[str, object]],
    ) -> bool | dict[str, object]:
        for comment in comments:
            if _classify(str(comment.get("body") or "")) == "evidence":
                return True
        try:
            issues = self.github.list_issues(state="all")  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        for item in issues:
            if coord.is_pull_request(item):
                continue
            try:
                item_number = int(item.get("number") or 0)
            except (TypeError, ValueError):
                continue
            if item_number == number:
                continue
            labels = coord.label_names(item.get("labels") or [])
            kind = coord.kind_from_title(str(item.get("title") or ""))
            if "type:evidence" not in labels and kind != "evidence":
                continue
            if _mentions_issue(str(item.get("body") or ""), number):
                return True
        return False

    def _pull_requirement(self, issue: Mapping[str, object], comments: Sequence[Mapping[str, object]]) -> str:
        body = str(issue.get("body") or "")
        text = "\n".join([body, *[str(comment.get("body") or "") for comment in comments]])
        links = list(_PULL_URL_RE.finditer(text))
        if links:
            repo = self.repo.lower()
            if any(f"{match.group('owner')}/{match.group('name')}".lower() != repo for match in links):
                return "outside"
            return "required"
        sections = coord.sections(body)
        described = "\n".join(
            sections.get(name, "") for name in ("Outcome", "Context", "Out of scope")
        )
        if _NO_PULL_RE.search(described):
            return "waived"
        project = coord.first_line(sections.get("Project")).lower()
        if project == "other" and not _WANTS_PULL_RE.search(described):
            return "waived"
        if _WANTS_PULL_RE.search(described):
            return "required"
        return "unknown"

    def _pulls_are_complete(
        self,
        issue: Mapping[str, object],
        comments: Sequence[Mapping[str, object]],
    ) -> dict[str, object] | None:
        text = "\n".join([str(issue.get("body") or ""), *[str(comment.get("body") or "") for comment in comments]])
        numbers: list[int] = []
        for match in _PULL_URL_RE.finditer(text):
            pull_number = int(match.group("number"))
            if pull_number not in numbers:
                numbers.append(pull_number)
        if not numbers:
            return err(
                "not_done",
                "A pull request is required, but no pull request URL or number is linked. status:done was not set.",
            )
        for pull_number in numbers:
            try:
                pull = self.github.get_pull(pull_number)  # type: ignore[union-attr]
                pull_comments = self.github.list_comments(pull_number)  # type: ignore[union-attr]
                reactions = self.github.list_reactions(pull_number)  # type: ignore[union-attr]
            except GitHubError as exc:
                return self._github_err(exc)
            if pull.get("merged") is not True:
                return err(
                    "not_done",
                    "The linked pull request is not merged. status:done was not set.",
                    pull_number=pull_number,
                )
            head = ""
            head_obj = pull.get("head")
            if isinstance(head_obj, dict):
                head = str(head_obj.get("sha") or "")
            if not self._review_passed(pull_comments, reactions, head):
                return err(
                    "not_done",
                    "The linked pull request has no passing Codex review of its head. status:done was not set.",
                    pull_number=pull_number,
                    head_sha=head,
                )
        return None

    def _review_passed(
        self,
        comments: Sequence[Mapping[str, object]],
        reactions: Sequence[Mapping[str, object]],
        head: str,
    ) -> bool:
        if not head:
            return False
        rounds, _pending = completed_reviews(comments)
        matched = [item for item in rounds if _same_commit(str(item["sha"]), head)]
        if not matched:
            return False
        latest = matched[-1]
        if latest.get("source") != "summary" or latest.get("unresolved") is not False:
            return False
        for reaction in reactions:
            if str(reaction.get("content") or "") != "+1":
                continue
            if _CODEX_BOT in _author(reaction).lower():
                return True
        return False

    def _needs_owner(self, issue: Mapping[str, object]) -> bool:
        labels = coord.label_names(issue.get("labels") or [])
        if "status:needs-owner" in labels:
            return True
        title = str(issue.get("title") or "")
        if coord.kind_from_title(title) == "decision":
            return True
        # Hygiene may not have copied Approval needed: yes onto the label yet.
        return coord.validate_issue(title, str(issue.get("body") or "")).needs_owner

    def _expected_role(self, issue: Mapping[str, object]) -> str | None:
        parsed = coord.sections(str(issue.get("body") or ""))
        kind = coord.kind_from_title(str(issue.get("title") or ""))
        if kind == "handoff":
            raw = coord.first_line(parsed.get("To"))
        elif kind == "task":
            raw = coord.first_line(parsed.get("Role"))
        else:
            return None
        if not raw or raw == "_No response_":
            return None
        return raw.lower()

    def _winning_claim(self, issue_number: int, issue: Mapping[str, object], hours: int) -> dict[str, object]:
        title = str(issue.get("title") or f"#{issue_number}")
        current = self._aware_now()
        body = claim_comment(
            agent=self.agent,
            claimed_at=stamp(current),
            expires=stamp(self._expiry(hours)),
            task=title,
        )
        posted = self._comment(issue_number, body)
        if isinstance(posted, dict) and posted.get("ok") is False:
            return posted
        fresh = self._assess(issue_number)
        if isinstance(fresh, dict) and fresh.get("ok") is False:
            return fresh
        if fresh.status != "active" or fresh.agent != self.agent:
            return err(
                "lease_conflict",
                "The claim did not become the live lease.",
                holder=fresh.agent,
                lease_id=fresh.lease_id,
                lease=lease_view(fresh),
            )
        labeled = self._set_status(issue_number, issue, "status:claimed", agent=self.agent, drop_expired=True)
        if isinstance(labeled, dict) and labeled.get("ok") is False:
            return labeled
        return ok(
            {
                "issue_number": issue_number,
                "held": True,
                "already_held": False,
                "comment_id": str(posted["id"]),  # type: ignore[index]
                "lease": lease_view(fresh),
                "identity": self._identity(),
            }
        )

    def _losing_claim(
        self,
        issue_number: int,
        issue: Mapping[str, object],
        hours: int,
        holder: str | None,
    ) -> dict[str, object]:
        title = str(issue.get("title") or f"#{issue_number}")
        current = self._aware_now()
        body = claim_comment(
            agent=self.agent,
            claimed_at=stamp(current),
            expires=stamp(self._expiry(hours)),
            task=title,
        )
        posted = self._comment(issue_number, body)
        if isinstance(posted, dict) and posted.get("ok") is False:
            return posted
        try:
            comments = self.github.list_comments(issue_number)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        state = coord.assess_lease(comments, self._aware_now())
        if state.status == "active" and state.agent == self.agent:
            labeled = self._set_status(issue_number, issue, "status:claimed", agent=self.agent, drop_expired=True)
            if isinstance(labeled, dict) and labeled.get("ok") is False:
                return labeled
            return ok(
                {
                    "issue_number": issue_number,
                    "held": True,
                    "already_held": False,
                    "comment_id": str(posted["id"]),  # type: ignore[index]
                    "lease": lease_view(state),
                    "identity": self._identity(),
                }
            )
        if coord.conflict_comment_needed(comments, state):
            conflict = self._comment(
                issue_number,
                conflict_comment(self.agent, coord.lease_conflict_comment(state)),
            )
            if isinstance(conflict, dict) and conflict.get("ok") is False:
                return conflict
        return err(
            "lease_conflict",
            f"{holder or state.agent} holds the live lease. This claim was recorded and did not replace it.",
            holder=state.agent,
            lease_id=state.lease_id,
            expires=stamp(state.expires) if state.expires is not None else None,
            marker=coord.lease_conflict_marker(state),
            attempt_comment_id=str(posted["id"]),  # type: ignore[index]
        )

    def _set_status(
        self,
        number: int,
        issue: Mapping[str, object],
        status: str,
        *,
        agent: str | None,
        drop_expired: bool,
    ) -> dict[str, object] | None:
        current = coord.label_names(issue.get("labels") or [])
        remove: list[str] = []
        agent_label = f"agent:{agent}" if agent else None
        for name in current:
            if name in coord.STATUS_LABELS and name != status:
                remove.append(name)
            elif drop_expired and name == "lease:expired":
                remove.append(name)
            elif agent_label and name.startswith("agent:") and name != agent_label:
                remove.append(name)
        add: list[str] = []
        if status not in current:
            add.append(status)
        if agent_label and agent_label not in current:
            add.append(agent_label)
        try:
            for name in remove:
                self.github.remove_label(number, name)  # type: ignore[union-attr]
            self.github.add_labels(number, add)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        return None

    def _comment(self, number: int, body: str) -> Mapping[str, object] | dict[str, object]:
        try:
            return self.github.create_comment(number, body)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)

    def _assess(self, number: int) -> coord.LeaseState | dict[str, object]:
        try:
            comments = self.github.list_comments(number)  # type: ignore[union-attr]
        except GitHubError as exc:
            return self._github_err(exc)
        return coord.assess_lease(comments, self._aware_now())

    def _not_holding(self, state: coord.LeaseState) -> dict[str, object]:
        if state.status == "expired":
            return err("lease_expired", "The lease has expired.", lease=lease_view(state))
        if state.status == "active":
            return err(
                "lease_not_holder",
                f"{state.agent} holds the lease.",
                holder=state.agent,
                lease_id=state.lease_id,
                lease=lease_view(state),
            )
        return err("lease_not_holder", "This agent does not hold a live lease.", lease=lease_view(state))

    def _github_err(self, exc: GitHubError) -> dict[str, object]:
        if exc.status == 404:
            return err("not_found", "GitHub could not find that issue or pull request.", status=404)
        return err("github_error", exc.message, status=exc.status)

    def _summary(self, issue: Mapping[str, object], lease: coord.LeaseState) -> dict[str, object]:
        labels = coord.label_names(issue.get("labels") or [])
        return {
            "number": issue.get("number"),
            "title": issue.get("title"),
            "state": issue.get("state"),
            "html_url": issue.get("html_url"),
            "kind": coord.kind_from_title(str(issue.get("title") or "")),
            "labels": labels,
            "role": self._expected_role(issue),
            "lease": lease_view(lease),
            "stall": stall_view(labels, lease),
        }

    def _matches(
        self,
        issue: Mapping[str, object],
        *,
        kind: str | None,
        status: str | None,
        project: str | None,
        role: str | None,
    ) -> bool:
        title_kind = coord.kind_from_title(str(issue.get("title") or ""))
        if kind and kind != "all" and title_kind != kind:
            return False
        labels = coord.label_names(issue.get("labels") or [])
        if status:
            wanted = status if status.startswith("status:") else f"status:{status}"
            if wanted not in labels:
                return False
        if project:
            wanted_project = project if project.startswith("project:") else f"project:{project}"
            section = coord.first_line(coord.sections(str(issue.get("body") or "")).get("Project")).lower()
            if wanted_project not in labels and section != project.lower() and section != wanted_project.removeprefix("project:"):
                return False
        if role:
            expected = self._expected_role(issue)
            if expected != role.strip().lower():
                return False
        return True


def service_from_env(now: Callable[[], datetime] | None = None) -> CoordinationService:
    agent = os.environ.get("EGREGORE_AGENT", "").strip().lower()
    token = os.environ.get("EGREGORE_GITHUB_TOKEN", "").strip()
    repo = os.environ.get("EGREGORE_REPO", "AStrasler/Egeregore").strip()
    api = os.environ.get("EGREGORE_GITHUB_API", "https://api.github.com").strip() or "https://api.github.com"
    problems: list[str] = []
    if agent not in coord.ALLOWED_AGENTS:
        problems.append("EGREGORE_AGENT")
    if not token:
        problems.append("EGREGORE_GITHUB_TOKEN")
    if not coord.REPO_RE.fullmatch(repo):
        problems.append("EGREGORE_REPO")
    github = None
    if not problems:
        github = RestGitHub(api=api, token=token, repo=repo)
    return CoordinationService(github, agent, repo=repo, now=now, config_problems=problems)
