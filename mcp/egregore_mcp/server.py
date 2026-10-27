"""MCP entrypoint. stdio is the default. Streamable HTTP is local only."""

from __future__ import annotations

import os
import sys

from typing import Any

from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from mcp.server import MCPServer

from egregore_mcp import __version__
from egregore_mcp.service import CoordinationService, service_from_env

INSTRUCTIONS = (
    "Egregore coordination tools for GitHub issues in this repository. "
    "EGREGORE_AGENT is the role written on the Agent line. "
    "The GitHub token's login is the comment author. A shared token for @AStrasler "
    "is still @AStrasler. This is not a separate GitHub identity. "
    "These tools do not merge, deploy, change settings, change permissions, or record "
    "Decision: approve or Decision: reject. "
    "request_review posts the single line @codex review on an open pull request and stops after "
    "three completed Codex reviews. It does not post on a closed pull request or repeat the same head."
)

_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)

class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResponse(BaseModel):
    ok: bool
    result: dict[str, Any] | None = None
    error: ErrorBody | None = None


def _response(payload: dict[str, object]) -> ToolResponse:
    return ToolResponse.model_validate(payload)


TOOLS = [
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
]


def build_server(service: CoordinationService | None = None) -> MCPServer:
    bound = service if service is not None else service_from_env()
    server = MCPServer(
        "egregore",
        title="Egregore",
        version=__version__,
        instructions=INSTRUCTIONS,
    )

    @server.tool(annotations=_READ)
    def get_tasks(
        state: str = "open",
        kind: str = "task",
        status: str = "",
        project: str = "",
        role: str = "",
        limit: int = 30,
    ) -> ToolResponse:
        """List coordination issues and the lease each one currently holds."""
        return _response(bound.get_tasks(
            state=state,
            kind=kind or None,
            status=status or None,
            project=project or None,
            role=role or None,
            limit=limit,
        ))

    @server.tool(annotations=_READ)
    def get_task(issue_number: int) -> ToolResponse:
        """Read one issue, its fields, and the lease held on it."""
        return _response(bound.get_task(issue_number))

    @server.tool(annotations=_WRITE)
    def claim_task(issue_number: int, lease_hours: int = 6) -> ToolResponse:
        """Claim a ready issue. The earliest live lease wins. A later agent is a conflict."""
        return _response(bound.claim_task(issue_number, lease_hours))

    @server.tool(annotations=_WRITE)
    def renew_lease(issue_number: int, lease_hours: int = 6, note: str = "") -> ToolResponse:
        """Renew the caller's live lease. The original claim comment id stays."""
        return _response(bound.renew_lease(issue_number, lease_hours, note))

    @server.tool(annotations=_WRITE)
    def release_task(issue_number: int, status: str, reason: str) -> ToolResponse:
        """Release the caller's lease. status:done needs evidence, and a linked pull request must be merged and reviewed."""
        return _response(bound.release_task(issue_number, status, reason))

    @server.tool(annotations=_WRITE)
    def post_progress(issue_number: int, done: str, next: str) -> ToolResponse:
        """Post a progress comment. The caller must hold the live lease."""
        return _response(bound.post_progress(issue_number, done, next))

    @server.tool(annotations=_WRITE)
    def handoff_task(
        issue_number: int,
        to_agent: str,
        done_so_far: str,
        next_action: str,
        title: str = "",
    ) -> ToolResponse:
        """Open a handoff issue for another role and release the source lease. It does not grant access."""
        return _response(bound.handoff_task(issue_number, to_agent, done_so_far, next_action, title))

    @server.tool(annotations=_WRITE)
    def request_review(pull_number: int) -> ToolResponse:
        """Post one @codex review on an open pull request. Closed pulls, a repeat of the same head, and a fourth round are refused."""
        return _response(bound.request_review(pull_number))

    @server.tool(annotations=_READ)
    def get_owner_decisions(state: str = "open", limit: int = 30) -> ToolResponse:
        """List owner-decision issues and Decision comments. recorded is not a verified approval."""
        return _response(bound.get_owner_decisions(state=state, limit=limit))

    @server.tool(annotations=_READ)
    def get_task_history(issue_number: int) -> ToolResponse:
        """Read the comment history of one issue, with secrets redacted."""
        return _response(bound.get_task_history(issue_number))

    return server


def http_bind() -> tuple[str, int]:
    host = os.environ.get("EGREGORE_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("EGREGORE_HTTP_HOST must be 127.0.0.1 or localhost")
    raw_port = os.environ.get("EGREGORE_HTTP_PORT", "8000").strip() or "8000"
    if not raw_port.isdigit():
        raise SystemExit("EGREGORE_HTTP_PORT must be an integer")
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise SystemExit("EGREGORE_HTTP_PORT must be from 1 to 65535")
    return host, port


def main() -> None:
    transport = os.environ.get("EGREGORE_TRANSPORT", "stdio").strip() or "stdio"
    server = build_server()
    if transport == "stdio":
        server.run(transport="stdio")
        return
    if transport == "streamable-http":
        host, port = http_bind()
        server.run(transport="streamable-http", host=host, port=port)
        return
    raise SystemExit("EGREGORE_TRANSPORT must be stdio or streamable-http")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
