# Egregore MCP v0.1

A local MCP server for coordination issues in [AStrasler/Egeregore](https://github.com/AStrasler/Egeregore). GitHub is the backend. Lease rules, conflict markers, and owner-decision notes come from [`scripts/coordination.py`](../scripts/coordination.py). This package does not copy that logic.

v0.1 does not merge, deploy, change settings, change permissions, or post `Decision: approve` or `Decision: reject`.

## Setup

Python 3.12 or newer. The only direct dependency is the MCP Python SDK, pinned in `mcp/requirements.txt`.

```bash
python3 -m pip install -r mcp/requirements.txt
export EGREGORE_GITHUB_TOKEN="paste locally, never commit"
export EGREGORE_AGENT="cursor"
export EGREGORE_REPO="AStrasler/Egeregore"
PYTHONPATH=mcp python3 -m egregore_mcp
```

`EGREGORE_AGENT` is `chatgpt`, `grokbot`, `cursor`, `codex`, or `aaron`. The process writes it as an `Agent:` line on every comment except a review request.

`EGREGORE_GITHUB_TOKEN` is a GitHub token that can read and write issues in this repo. The server reads it from the environment only. Do not put it in a file that gets committed.

Default transport is stdio. Streamable HTTP stays on localhost:

```bash
EGREGORE_TRANSPORT=streamable-http EGREGORE_HTTP_PORT=8765 PYTHONPATH=mcp python3 -m egregore_mcp
```

Clients use `http://127.0.0.1:8765/mcp`. `EGREGORE_HTTP_HOST` accepts `127.0.0.1` or `localhost` only.

## Tools

| Tool | Effect |
| --- | --- |
| `get_tasks` | List issues. Default kind is `task`. Filters: `state`, `kind`, `status`, `project`, `role`, `limit`. |
| `get_task` | One issue, its form fields, and the current lease. |
| `claim_task` | Claim for 1–24 hours. Default 6. The earliest live claim wins. |
| `renew_lease` | The holder posts a new expiry. The original claim comment id stays. |
| `release_task` | The holder releases and sets `ready`, `blocked`, `needs-review`, `needs-owner`, or `done`. |
| `post_progress` | Progress comment from the holder. Moves `status:claimed` to `status:in-progress`. |
| `handoff_task` | Opens a `[Handoff]` issue for another role and releases the source lease. |
| `request_review` | Posts a pull-request comment whose body is exactly `@codex review`. |
| `get_owner_decisions` | Decision issues and `Decision:` comments. |
| `get_task_history` | Comment history with credential-shaped text redacted. |

A result is JSON: `{"ok": true, "result": ...}` or `{"ok": false, "error": {"code": "...", "message": "...", "details": {}}}`.

Error codes: `invalid_input`, `not_found`, `lease_conflict`, `lease_not_holder`, `lease_expired`, `not_claimable`, `wrong_role`, `review_limit`, `review_current`, `review_same_head`, `review_pending`, `forbidden`, `secret_rejected`, `github_error`, `unconfigured`.

`lease_conflict` means another agent holds the live lease. The losing claim is stored, labels stay with the holder, and the conflict marker from `coordination.py` names that holder's claim comment id.

`get_owner_decisions` returns `authorization` of `recorded` or `none`. It never returns `verified`. A comment from `@AStrasler` is a recorded note. The server does not change labels from it.

`request_review` allows three completed Codex reviews of distinct commits. A fourth request returns `review_limit`. A receipt that already names the current head with no unresolved findings returns `review_current`. Findings on that same head return `review_same_head`. Push a new commit before asking again.

## Cursor

Put this in the Cursor MCP config for this machine. The token stays in that local config, not in the repo. `PYTHONPATH` is the absolute path of this `mcp/` directory.

```json
{
  "mcpServers": {
    "egregore": {
      "command": "python3",
      "args": ["-m", "egregore_mcp"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/Egeregore/mcp",
        "EGREGORE_AGENT": "cursor",
        "EGREGORE_REPO": "AStrasler/Egeregore",
        "EGREGORE_GITHUB_TOKEN": "local token, not committed"
      }
    }
  }
}
```

## Codex

Codex reviews pull requests through its GitHub app when a comment is `@codex review`. To call these tools itself, point a Codex MCP client at the same stdio command with `EGREGORE_AGENT` set to `codex`. One current Codex CLI shape is:

```toml
[mcp_servers.egregore]
command = "python3"
args = ["-m", "egregore_mcp"]

[mcp_servers.egregore.env]
PYTHONPATH = "/absolute/path/to/Egeregore/mcp"
EGREGORE_AGENT = "codex"
EGREGORE_REPO = "AStrasler/Egeregore"
EGREGORE_GITHUB_TOKEN = "local token, not committed"
```

The table name can differ by Codex version. The command, the agent id, and the token are the part that matters.

## Identity

Every comment this server writes starts with `Agent: Cursor`, `Agent: Codex`, `Agent: Grokbot`, `Agent: ChatGPT`, or `Agent: Aaron`, matching `EGREGORE_AGENT`. Lease comments also include `Claim-Agent`, which `scripts/coordination.py` reads.

The review comment is the exception. Codex starts a review when the pull-request comment is the single line `@codex review`. An `Agent:` line on that comment would hide the request, so `request_review` does not add one.

GitHub attributes the comment to the user who owns `EGREGORE_GITHUB_TOKEN`. A token for `@AStrasler` means every agent still posts as `@AStrasler`. The `Agent` line is a role name. It is not a login, and it is not Aaron's approval.

This is not per-agent authentication. A real separation needs a GitHub App or a token per role. Creating either one is an owner decision. This server does not create them.

## Tests

```bash
python3 -m pip install -r mcp/requirements.txt
python3 -m unittest discover -s tests -v
```

Unit tests call the tools with an in-memory GitHub. The protocol test starts the server over stdio, lists the tools, and calls some of them. The two-client test runs Cursor and Codex against that same fake GitHub: Cursor claims, Codex sees the claim and loses a conflicting claim, Cursor posts progress and a handoff, and Codex reads the handoff. CI runs that suite. It does not call the real GitHub API.

## Open pull requests

This branch is based on `main`. It imports `scripts/coordination.py` and does not edit it.

- Pull request #11 changes `scripts/coordination.py` (stall notes, label copy, closed-task status). Those helpers are additive. Rebase this branch after #11 merges and rerun the suite if `assess_lease`, `lease_conflict_comment`, `conflict_comment_needed`, `decision_plan`, or `validate_issue` change behavior.
- Pull request #9 documents the `Agent:` line and the three-review limit, and it also edits `README.md`. This branch edits a different `README.md` sentence. A rebase may still be needed if those hunks meet.

## Limits

Hosting is in [HOSTING.md](HOSTING.md). The short version: run it locally. Do not deploy it from this pull request.

Issue comments are not a lock. Two clients can both read `status:ready` before either comment is visible. The earlier live claim wins after GitHub stores both comments.

A live test against this repository was not run from the implementation environment. That environment has no `EGREGORE_GITHUB_TOKEN`, and its `gh` login cannot write issues here. Do not copy a token out of another session to force that test.
