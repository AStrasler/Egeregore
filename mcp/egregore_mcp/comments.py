"""Comment and issue text for the coordination MCP server.

Lease lines match OPERATIONS.md. scripts/coordination.py parses Claim-Agent,
Lease-Expires, and Lease-Release. The Agent line is the role. It is not a
GitHub login. A review request is the exception: Codex starts a review only
when the pull-request comment is the single line `@codex review`.
"""

from __future__ import annotations

import re

DISPLAY_NAMES = {
    "chatgpt": "ChatGPT",
    "grokbot": "Grokbot",
    "cursor": "Cursor",
    "codex": "Codex",
    "aaron": "Aaron",
}

REVIEW_COMMENT = "@codex review"

# Same anchors as scripts/coordination.py. assess_lease matches these after
# stripping the line, including inside a Markdown fence.
_LEASE_CONTROL_RES = (
    re.compile(r"^Lease-Expires:\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s*$"),
    re.compile(r"^Claim-Agent:\s*[A-Za-z]+\s*$"),
    re.compile(r"^Lease-Release:\s*[A-Za-z]+\s*$"),
)

ACCESS_SENTENCE = (
    "This handoff does not grant repository access, data access, credentials, "
    "or permission to merge or deploy."
)


def display_name(agent: str) -> str:
    return DISPLAY_NAMES[agent]


def agent_preamble(agent: str) -> str:
    return f"Agent: {display_name(agent)}"


def contains_lease_control(value: str) -> bool:
    """True when a free-form field contains a line assess_lease would apply."""
    for raw in value.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if any(pattern.match(line) for pattern in _LEASE_CONTROL_RES):
            return True
    return False


def conflict_comment(agent: str, body: str) -> str:
    """Keep the coordination marker and name the role that posted it."""
    return f"{agent_preamble(agent)}\n\n{body.lstrip(chr(10))}"


def claim_comment(*, agent: str, claimed_at: str, expires: str, task: str) -> str:
    return "\n".join(
        [
            agent_preamble(agent),
            "",
            "## Claim",
            f"Claim-Agent: {agent}",
            f"Claimed-At: {claimed_at}",
            f"Lease-Expires: {expires}",
            f"Task: {task}",
            "Data-Boundary: coordination-only",
            "",
        ]
    )


def renewal_comment(*, agent: str, expires: str, note: str) -> str:
    lines = [
        agent_preamble(agent),
        "",
        "## Lease renewal",
        f"Claim-Agent: {agent}",
        f"Lease-Expires: {expires}",
    ]
    if note.strip():
        lines.append(f"Note: {note.strip()}")
    lines.extend(["Data-Boundary: coordination-only", ""])
    return "\n".join(lines)


def release_comment(*, agent: str, reason: str) -> str:
    return "\n".join(
        [
            agent_preamble(agent),
            "",
            "## Lease release",
            f"Claim-Agent: {agent}",
            f"Lease-Release: {agent}",
            f"Reason: {reason.strip()}",
            "Data-Boundary: coordination-only",
            "",
        ]
    )


def progress_comment(*, agent: str, done: str, next_step: str) -> str:
    return "\n".join(
        [
            agent_preamble(agent),
            "",
            "## Progress",
            f"Claim-Agent: {agent}",
            f"Done: {done.strip()}",
            f"Next: {next_step.strip()}",
            "Data-Boundary: coordination-only",
            "",
        ]
    )


def handoff_issue_body(
    *,
    project: str,
    project_slug: str,
    source_agent: str,
    to_agent: str,
    issue_number: int,
    done_so_far: str,
    next_action: str,
    repo: str,
    boundary: str,
) -> str:
    access = (
        f"{ACCESS_SENTENCE} The work stays in {repo}. "
        "This issue does not add access there."
    )
    lines = [
        "### Project",
        "",
        project,
        "",
    ]
    if project_slug:
        lines.extend(["### Project slug", "", project_slug, ""])
    lines.extend(
        [
            "### From",
            "",
            source_agent,
            "",
            "### To",
            "",
            to_agent,
            "",
            "### Related issue",
            "",
            f"#{issue_number}",
            "",
            "### Done so far",
            "",
            done_so_far.strip(),
            "",
            "### Next action",
            "",
            next_action.strip(),
            "",
            "### Access",
            "",
            access,
            "",
            "### Data boundary",
            "",
            f"- [x] {boundary}",
            "",
        ]
    )
    return "\n".join(lines)


def handoff_note(*, agent: str, to_agent: str, handoff_number: int) -> str:
    return "\n".join(
        [
            agent_preamble(agent),
            "",
            "## Handoff",
            f"Claim-Agent: {agent}",
            f"Lease-Release: {agent}",
            f"To: {to_agent}",
            f"Handoff-Issue: #{handoff_number}",
            f"Reason: next step belongs to {to_agent}",
            "Data-Boundary: coordination-only",
            "",
        ]
    )
