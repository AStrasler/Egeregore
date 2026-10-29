# Egregore MCP v0.1

A local MCP server for coordination issues in [AStrasler/Egeregore](https://github.com/AStrasler/Egeregore). GitHub is the backend. Lease rules, conflict markers, and owner-decision notes come from [`scripts/coordination.py`](../scripts/coordination.py). This package does not copy that logic.

v0.1 does not merge, deploy, change settings, change permissions, or post `Decision: approve` or `Decision: reject`.

## Setup

Python 3.12 or newer. Direct dependencies are pinned in `mcp/requirements.txt`: the MCP Python SDK, plus PyJWT and cryptography for the GitHub App JWT. The standard library cannot sign RS256. PyJWT is the small, well-known JWT library GitHub's own App examples use, and cryptography is the RS256 backend it imports. Both are free (MIT, and Apache-2.0/BSD). The repo client itself stays on the standard-library HTTP stack.

```bash
python3 -m pip install -r mcp/requirements.txt
export EGREGORE_AGENT="cursor"
export EGREGORE_REPO="AStrasler/Egeregore"
PYTHONPATH=mcp python3 -m egregore_mcp
```

`EGREGORE_AGENT` is `chatgpt`, `grokbot`, `cursor`, `codex`, or `aaron`. The process writes it as an `Agent:` line on every comment except a review request.

### GitHub App

Aaron creates the private GitHub App and installs it only on `AStrasler/Egeregore`. This repository does not create the app, install it, or store its key. Expected installation permissions are Issues read and write, Pull requests read-only, and Metadata read-only. Do not grant Contents, Administration, Members, or anything that can merge or change repository settings.

The server signs an RS256 JWT with the app private key (`iat` one minute in the past, `exp` eight minutes ahead, `iss` the app id) and exchanges it with `POST /app/installations/{id}/access_tokens`. That request asks only for the repository name in `EGREGORE_REPO` and only for `issues:write`, `pull_requests:read`, and `metadata:read`. The installation token is cached and refreshed five minutes before `expires_at`. A token that expires sooner than that window is used until it actually expires, then refreshed. If a refresh fails and the cached token is still unexpired, the server keeps using it. A refresh that comes back with a wider repository list stops the process.

The process refuses to start when the installation token's repository list is anything other than `EGREGORE_REPO`, when `repository_selection` is `all`, or when the returned permissions are broader or narrower than the three above. It does not call merge, ruleset, or permission APIs.

```bash
export EGREGORE_APP_ID="the numeric app id"
export EGREGORE_APP_INSTALLATION_ID="optional; omit to discover it"
export EGREGORE_APP_PRIVATE_KEY_PATH="/absolute/path/outside/this/repo/egregore-app.pem"
# or, instead of the path, the PEM text itself (real newlines or \n escapes):
# export EGREGORE_APP_PRIVATE_KEY="paste the PEM here, never commit it"
```

`EGREGORE_APP_INSTALLATION_ID` is optional. When it is unset, the server discovers the id with `GET /repos/{owner}/{repo}/installation` using the app JWT. When `EGREGORE_APP_PRIVATE_KEY` is non-empty it wins over `EGREGORE_APP_PRIVATE_KEY_PATH`. Any of the app variables selects app mode. A partial set refuses to start, including when `EGREGORE_GITHUB_TOKEN` is also set, so a missing key cannot silently fall back to a personal token.

Keep the private key out of the repo. `.gitignore` ignores `*.pem`. Do not commit a key, and do not paste one into an issue.

### Personal token fallback

When none of the app variables are set, the server uses `EGREGORE_GITHUB_TOKEN`, the same local path as v0.1. The process reads it from the environment only.

```bash
export EGREGORE_GITHUB_TOKEN="paste locally, never commit"
```

That token needs to be able to read and write issues in this repo. A token for `@AStrasler` still posts as `@AStrasler`. Use it for local runs before the app exists. Do not put it in a file that gets committed.

Default transport is stdio. Streamable HTTP stays on localhost:

```bash
EGREGORE_TRANSPORT=streamable-http EGREGORE_HTTP_PORT=8765 PYTHONPATH=mcp python3 -m egregore_mcp
```

Clients use `http://127.0.0.1:8765/mcp`. `EGREGORE_HTTP_HOST` accepts `127.0.0.1` or `localhost` only.

## Tools

| Tool | Effect |
| --- | --- |
| `get_tasks` | List issues. Default kind is `task`. Filters: `state`, `kind`, `status`, `project`, `role`, `limit`. |
| `get_task` | One issue, its form fields, the current lease, and the stall gate. |
| `claim_task` | Claim an open `type:task` that is `status:ready`, or reclaim one that is `status:claimed` or `status:in-progress` after the lease expired. The role named in `To` may claim a ready handoff on the same rules. Default 6 hours, maximum 24. The earliest live claim wins. Evidence, review, and decision issues are refused, as are closed issues and `status:blocked` or `status:needs-review`. |
| `renew_lease` | The holder posts a new expiry. The original claim comment id stays. Re-reads the issue and refuses a closed, done, blocked, or owner-gated issue. |
| `release_task` | The holder releases and sets `ready`, `blocked`, `needs-review`, `needs-owner`, or `done`. `status:done` needs evidence. A linked pull request in this repo must be merged, with a Codex summary of no unresolved findings on that head and a 👍 from `chatgpt-codex-connector`. A required pull request with no link returns `not_done`. Docs, research, or project `other` tasks that need no pull request skip that check. Owner approval is not bypassed. |
| `post_progress` | Progress comment from the holder. Re-reads the issue and refuses a closed, done, blocked, or owner-gated issue. Moves `status:claimed` to `status:in-progress`. |
| `handoff_task` | Opens a `[Handoff]` issue for another role and releases the source lease. The same closed, done, blocked, and owner gates apply. |
| `request_review` | Posts a pull-request comment whose body is exactly `@codex review`. Refuses a closed or merged pull request, a second request for the same head, and a fourth completed round. |
| `get_owner_decisions` | Decision issues and `Decision:` comments. |
| `get_task_history` | Comment history with credential-shaped text redacted. |
| `get_specialists` | Read-only discovery of six pinned agent/skill references (five from `AStrasler/agents` and optional `DietrichGebert/ponytail`) (filters: `role`, `kind`, `specialist_id`). Does not load or run prompts. |

A result is JSON: `{"ok": true, "result": ...}` or `{"ok": false, "error": {"code": "...", "message": "...", "details": {}}}`.

Error codes: `invalid_input`, `not_found`, `lease_conflict`, `lease_not_holder`, `lease_expired`, `not_claimable`, `not_done`, `not_reviewable`, `wrong_role`, `review_limit`, `review_current`, `review_same_head`, `review_duplicate`, `review_pending`, `forbidden`, `secret_rejected`, `github_error`, `unconfigured`.

`lease_conflict` means another agent holds the live lease. The losing claim is stored, labels stay with the holder, and the conflict marker from `coordination.py` names that holder's claim comment id.

`get_owner_decisions` returns `authorization` of `recorded` or `none`. It never returns `verified`. A comment from `@AStrasler` is a recorded note. The server does not change labels from it. That matches `decision_plan`: a login records the note and does not authorize a merge, a setting change, or a label move.

`stall` on a task is the same gate lease watch uses. `reason` comes from `stall_reason` (`needs-review`, `handoff`, `needs-owner`, or null). `flagged` is whether `stall:flagged` is already on the issue. `lease_blocks_flag` is true when `assess_lease` says the lease is active, which is when `stall_plan` refuses to flag. These tools do not post `<!-- egregore-stall: -->` and do not add or remove `stall:flagged`. A claim, progress note, or handoff is an ordinary comment, so it counts as newer activity for the next lease-watch run.

`request_review` allows three completed Codex reviews of distinct commits. A fourth request returns `review_limit`. A receipt that already names the current head with no unresolved findings returns `review_current`. Findings on that same head return `review_same_head`. Another record for that same head returns `review_duplicate`. A second request while a review is still running returns `review_pending`. A closed or merged pull request returns `not_reviewable` and posts nothing. Push a new commit before asking again. When a summary and a receipt name the same commit, the summary's `has_unresolved_findings` flag wins, including when the summary was edited after the receipt. A summary or receipt counts only when `chatgpt-codex-connector[bot]` authored it. A copy from another login does not. With the GitHub App token, pull requests are read-only, so this post may return `github_error`. Do not widen the app permission to make it succeed.

Renew, progress, handoff, and release re-read the issue immediately before they write. A closed issue, `status:done`, `status:blocked`, `status:needs-owner`, or `Approval needed: yes` returns `not_claimable` and posts nothing. An open task with a live lease can still renew, post progress, hand off, and release to `status:ready`.

`release_task` with `status:done` also returns `not_done` unless completion evidence is already on the task. Evidence is a comment whose heading is `## Evidence`, or an evidence issue that links this task with `#N`, `owner/repo#N`, or `https://github.com/owner/repo/issues/N` for this repository. A link to another repository does not count. For a linked pull request in this repository, the pull request must be merged, the Codex summary for that head must have `has_unresolved_findings` false, and the pull request must carry a 👍 from `chatgpt-codex-connector`. A clear receipt does not override a summary that still has findings. A pull request in another repository is not treated as merged. When the task requires a pull request and no pull request URL or number is linked, `status:done` returns `not_done`. A task or handoff needs no pull request when its outcome, context, out-of-scope, done-so-far, or next-action text says `no pull request`, `no pr`, `without a pull request`, `documentation only`, `docs only`, or `research only`, or when Project is `other` and it never asks for a pull request. `No merge` and `no deploy` do not waive a pull request. An explicit pull request requirement wins over that waiver. `status:done` still does not clear an owner gate.

## Cursor

Put this in the Cursor MCP config for this machine. The app private key, or the personal token, stays in that local config, not in the repo. `PYTHONPATH` is the absolute path of this `mcp/` directory.

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
        "EGREGORE_APP_ID": "numeric app id",
        "EGREGORE_APP_PRIVATE_KEY_PATH": "/absolute/path/outside/the/repo/egregore-app.pem"
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
EGREGORE_APP_ID = "numeric app id"
EGREGORE_APP_PRIVATE_KEY_PATH = "/absolute/path/outside/the/repo/egregore-app.pem"
```

The table name can differ by Codex version. The command, the agent id, and the credentials are the part that matters. For a local run before the app exists, set `EGREGORE_GITHUB_TOKEN` and leave the app variables unset.

## Identity

Every comment this server writes starts with `Agent: Cursor`, `Agent: Codex`, `Agent: Grokbot`, `Agent: ChatGPT`, or `Agent: Aaron`, matching `EGREGORE_AGENT`. Lease comments also include `Claim-Agent`, which `scripts/coordination.py` reads.

The review comment is the exception. Codex starts a review when the pull-request comment is the single line `@codex review`. An `Agent:` line on that comment would hide the request, so `request_review` does not add one.

A losing claim also posts the conflict note from `coordination.py`. That note keeps the `egregore-lease-conflict` marker, and this server adds the caller's `Agent:` line in front of it.

`done`, `next`, renewal notes, release reasons, claim titles, and handoff text quote a line that is `Claim-Agent`, `Lease-Expires`, or `Lease-Release`. The quote is a leading `>`. `assess_lease` strips indentation and still reads lines inside a fence, and the `>` keeps that line from matching. The note is posted. It does not release or renew a lease.

`claim_task` also refuses a body whose `Approval needed` line starts with `yes`, even when `status:needs-owner` is not on the issue yet. That is `validate_issue(...).needs_owner`. Renew, release, progress, and handoff use the same gate.

A handoff's Access field names the repository where the work happens. Coordination stays in this repo. WHOOP Lens stays at `AStrasler/WHOOP-Lens`. Another project keeps a repository URL already written on the task. It does not name this coordination repo in that sentence.

In GitHub App mode, GitHub attributes comments to the app's bot, for example `egregore[bot]`, not to `@AStrasler`. One installation serves every role that points at this server. The `Agent` line is still the role. It is not a login, and it is not Aaron's approval. The bot cannot merge: the token request does not ask for that, and there is no merge tool.

With the personal-token fallback, GitHub attributes the comment to the user who owns `EGREGORE_GITHUB_TOKEN`. A token for `@AStrasler` means every agent still posts as `@AStrasler`.

This is not a separate login per role. Creating the GitHub App, installing it, and holding the private key are owner decisions. This server does not create the app or change its permissions. `request_review` posts an issue comment on a pull request. Pull requests on the app token are read-only, so GitHub may reject that post with `github_error`. That refusal is not a reason to grant `pull_requests:write`. A local personal token can still post the mention when that token already has the scope.

## Tests

```bash
python3 -m pip install -r mcp/requirements.txt
python3 -m unittest discover -s tests -v
```

Unit tests call the tools with an in-memory GitHub. The protocol test starts the server over stdio, lists the tools, and calls some of them. The two-client test runs Cursor and Codex against that same fake GitHub: Cursor claims, Codex sees the claim and loses a conflicting claim, Cursor posts progress and a handoff, and Codex reads the handoff. `tests/test_github_app_auth.py` points the app client at a local token endpoint and covers expiry and refresh, a repository list or permission set that refuses to start, and the personal-token fallback. CI runs that suite. It does not call the real GitHub API and it has no app private key.

## Relationship to main

The package imports `scripts/coordination.py` and does not edit it. GitHub App authentication is optional. `EGREGORE_GITHUB_TOKEN` remains the fallback when the app variables are unset.

`assess_lease`, `lease_conflict_comment`, and `conflict_comment_needed` are the claim path. `decision_plan` is the owner-decision path, and its authorization stays `recorded` or `none`. `stall_plan` stays in lease watch. The MCP reads `stall_reason` and the `stall:flagged` label, and it does not flag stalls itself. A live lease is not idle under `stall_plan`.

## Specialist catalog

The optional specialist catalog is a **source-reference index**, not an agent runtime. `get_specialists` returns five pinned files in the [AStrasler/agents](https://github.com/AStrasler/agents) fork plus one optional coding skill from [Ponytail](https://github.com/DietrichGebert/ponytail). It does not copy prompt bodies, install skills, invoke specialists, add repositories, or grant any permission. Cursor/Codex must load chosen prompts separately using their own access and sandbox. See [docs/SPECIALISTS.md](../docs/SPECIALISTS.md).

## Limits

Hosting is in [HOSTING.md](HOSTING.md). The short version: run it locally. Do not deploy it from this pull request.

Issue comments are not a lock. Two clients can both read `status:ready` before either comment is visible. The earlier live claim wins after GitHub stores both comments.

A pull request linked outside this repository is not marked merged. This server's GitHub client reads only `EGREGORE_REPO`. A task that requires a pull request and does not link one is not marked done. A handoff is read the same way, including `Done so far` and `Next action`.

A live test against this repository was not run from the implementation environment. That environment has no app private key and no `EGREGORE_GITHUB_TOKEN`. Do not copy a token or a key out of another session to force that test. The live sequence, after Aaron creates the app, is [docs/phase5-live-test-plan.md](../docs/phase5-live-test-plan.md).
