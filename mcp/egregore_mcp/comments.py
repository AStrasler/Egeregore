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


def neutralize_lease_lines(value: str) -> str:
    """Quote lease-control lines so assess_lease ignores them.

    The parser strips indentation and still matches a line inside a fence.
    A leading ``>`` survives that strip and breaks the ``^Lease-`` anchor.
    """
    kept: list[str] = []
    for raw in value.replace("\r\n", "\n").split("\n"):
        if any(pattern.match(raw.strip()) for pattern in _LEASE_CONTROL_RES):
            kept.append("> " + raw.strip())
        else:
            kept.append(raw)
    return "\n".join(kept)


# Documented project repositories. coordination is this repo. WHOOP Lens is
# public at that URL. Weather Intelligence has no URL in this repo's docs.
PROJECT_REPOS = {
    "coordination": "https://github.com/AStrasler/Egeregore",
    "whoop-lens": "https://github.com/AStrasler/WHOOP-Lens",
}
_GITHUB_REPO_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def work_repository(project: str, project_slug: str, body: str) -> str:
    """Where the work happens. Never substitute the coordination repo for another project."""
    name = project.strip().lower()
    slug = project_slug.strip().lower()
    key = slug if name == "other" and slug else name
    if key in PROJECT_REPOS:
        return PROJECT_REPOS[key]
    for match in _GITHUB_REPO_RE.finditer(body or ""):
        url = match.group(0)
        if url.lower().endswith("/astrasler/egeregore"):
            continue
        return url
    if key == "weather-intelligence":
        return "the Weather Intelligence repository"
    label = project.strip() or "the project"
    return f"the {label} repository"


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
            "Task: " + neutralize_lease_lines(task),
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
        lines.append("Note: " + neutralize_lease_lines(note.strip()))
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
            "Reason: " + neutralize_lease_lines(reason.strip()),
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
            "Done: " + neutralize_lease_lines(done.strip()),
            "Next: " + neutralize_lease_lines(next_step.strip()),
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
            neutralize_lease_lines(done_so_far.strip()),
            "",
            "### Next action",
            "",
            neutralize_lease_lines(next_action.strip()),
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
