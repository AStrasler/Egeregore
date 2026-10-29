# Egregore on Cloudflare Workers (read and write tools, not reachable)

This directory is the hosted Egregore MCP server, built up in small pull requests. The design is in [docs/cloudflare-hosting.md](../docs/cloudflare-hosting.md). The local Python server in `mcp/` stays the working server until the later pull requests land.

**Status: not deployed and closed.** Merging this directory uploads a Worker *version* (Workers Builds runs `versions upload`) and deploys nothing. Every tool needs a bearer token that matches a hashed record in the `EGREGORE_AGENT_TOKENS` secret. No such secret is set, so even if a version were deployed, every tool would answer `unauthenticated` and none could reach GitHub. Aaron sets the secret himself. See "Caller authentication" below.

## Tools

| Tool | Reads | Matches Python |
| --- | --- | --- |
| `get_tasks` | issues, then each issue's comments (for the lease) | `get_tasks` |
| `get_task` | one issue and its comments | `get_task` |
| `get_task_history` | one issue's comment thread, classified | `get_task_history` |
| `get_owner_decisions` | Decision issues and their `Decision:` comments | `get_owner_decisions` |
| `get_specialists` | `mcp/specialists.json`, bundled at build time | `get_specialists` |
| `claim_task` | the issue, its comments; then a claim comment and the status and agent labels | `claim_task` |
| `renew_lease` | the issue and comments; then a renewal comment | `renew_lease` |
| `release_task` | the issue and comments (and, for `done`, the evidence and the linked pull requests); then a release comment and labels | `release_task` |
| `post_progress` | the issue and comments; then a progress comment and labels | `post_progress` |
| `handoff_task` | the issue and comments; then a new handoff issue, a note on the source, and labels | `handoff_task` |
| `request_review` | the pull request and its comments; then one `@codex review` comment | `request_review` |
| `ping` | nothing (kept from the scaffold; safe to remove) | none |

The write tools follow the Python server's order of reads, guards and writes step for step, so a refusal makes no write and a half-finished call leaves the same trace. Nothing merges, closes or approves anything. They are marked as writes (`readOnlyHint: false`, `destructiveHint: false`). With no valid token they answer `unauthenticated` before any GitHub call, with a valid token that lacks the permission, tool or repository they answer `forbidden`, and with no GitHub App settings they answer `unconfigured`.

Every tool returns the same `{ok, result, error}` envelope as the Python server, as `structuredContent` and as indent-2 JSON text. Envelope errors do not set `isError`, as in Python.

## Layout

- `wrangler.jsonc`: Worker `egeregore`, `workers_dev: false`, `preview_urls: false`, no routes, no bindings, no vars, no secrets.
- `src/index.ts`: the Worker. Stateless `createMcpHandler` at `/mcp`. Builds a fresh service, GitHub client and subrequest budget per request. Authenticates the bearer token, passes the caller's record to the access gate, and takes the agent from it.
- `src/auth.ts`: bearer-token lookup, the `EGREGORE_AGENT_TOKENS` parser, the permission model, and the safe `owner/name` check. It imports nothing, so the local scripts load the same code.
- `scripts/generate-agent-token.mjs`, `scripts/check-agent-tokens.mjs`: make a token and its record, and check a config. Local and offline.
- `src/github-app.ts`: GitHub App auth with WebCrypto. RS256 JWT, PKCS#1 to PKCS#8 conversion, an installation token limited to the `Egeregore` repository with `issues:write`, `pull_requests:read`, `metadata:read`. The token is checked after it is minted and refused if it is broader. Cached per isolate.
- `src/github.ts`: the GitHub REST client. Pagination that cannot leave the API origin, no redirects (so the bearer token never goes to another host), secret scrubbing, and a subrequest budget.
- `src/coordination.ts`: the comment-marker and lease rules, ported from `scripts/coordination.py` and `mcp/egregore_mcp/comments.py`. `src/pycompat.ts` holds the Python-versus-JavaScript string details (`strip`, `splitlines`, `\s`, `\d`, `fromisoformat`).
- `src/service.ts`, `src/specialists.ts`, `src/server.ts`: the five read tools and the six write tools. `src/comments.ts` builds the claim, renewal, progress, release, handoff and conflict comments. `src/reviews.ts` reads the Codex review state of a pull request. `src/rules.ts` holds the field and done-gate rules that the write tools share.
- `test/bearer-auth.test.ts`: the token layer end to end through the Worker. `test/parity/repo-names.json` and `test/parity/auth-model.json` are shared with Python tests (`tests/test_auth_model_parity.py`), so both sides refuse `../..` and agree on which tool needs which permission. The Python server has no token layer: it is one agent over stdio.
- `test/`: Vitest tests that run inside the Workers runtime. No network, no credentials; RSA keys are generated at test time.

## Caller authentication

Each agent gets its own bearer token. The Worker stores only the SHA-256 of each token, in the secret `EGREGORE_AGENT_TOKENS`, and the agent comes from the matching record. No tool takes an `agent` argument, and an extra one is ignored.

The secret is a JSON array. Each record says what one token may do:

```json
[
  {
    "sha256": "<64 hex characters, from the generator>",
    "agent": "cursor",
    "permissions": ["read", "write", "review"],
    "repos": ["AStrasler/Egeregore"],
    "tools": ["*"],
    "label": "cursor on the laptop"
  }
]
```

| Field | Rule |
| --- | --- |
| `sha256` | 64 hex characters. Unique in the array. |
| `agent` | `chatgpt`, `grokbot`, `cursor` or `codex`. Never `aaron`. |
| `permissions` | A non-empty list from `read`, `write`, `review`. One does not imply another. |
| `repos` | 1 to 16 explicit `owner/name` entries. No wildcard. Empty, `.` and `..` segments are refused. |
| `tools` | `["*"]`, or tool names from the table above. An unknown name is an error. |
| `label` | Optional, up to 80 characters, for people only. |

Permissions: `read` is `ping`, `get_tasks`, `get_task`, `get_task_history`, `get_owner_decisions` and `get_specialists`. `write` is `claim_task`, `renew_lease`, `release_task`, `post_progress` and `handoff_task`. `review` is `request_review`.

What the Worker answers, in order:

1. `unauthenticated` when the secret is unset or invalid, or the header is missing, malformed or does not match a record. The answer is the same in every case, so it says nothing about which part failed.
2. `forbidden` when the token's tool list excludes the tool, when it lacks the permission, or when the configured `EGREGORE_REPO` is not in its `repos` (or is not a plain `owner/name`).
3. `unconfigured` when the GitHub App settings are missing.

One bad record rejects the whole array, so a typo cannot leave a token with more access than intended. A refused call makes no GitHub call. `initialize` and `tools/list` return only fixed server metadata and need no token.

### Setup (Aaron does this; nothing here sets a secret)

1. Make a token per agent, on your own machine: `node worker/scripts/generate-agent-token.mjs --agent cursor`. Add `--permissions read,review`, `--repos owner/name` or `--tools get_tasks,get_task` to narrow it. The token prints once. The record holds only its hash.
2. Hand each token to that agent's client config. Do not commit it or paste it into chat.
3. Put the records into one JSON array in a file outside the repository, and check it: `node --experimental-strip-types worker/scripts/check-agent-tokens.mjs < tokens.json`. Add `--verify` with `TOKEN` set to test one token. The checker prints the agent and counts, never a token or a hash.
4. Set the array as the Worker secret `EGREGORE_AGENT_TOKENS` in the Cloudflare dashboard or with `wrangler secret put`. Setting it is Aaron's step. Nothing in this repository or its CI sets it.
5. To rotate, add a record for a new token, switch the client, then remove the old record. To revoke, remove the record.

The token check is one SHA-256 and a loop over at most 32 digests, so it stays far inside the 10 ms Free-plan CPU limit. The parsed config is cached per isolate.

## Free-plan limits

The Workers Free plan allows 50 subrequests and 10 ms of CPU per request, and 6 open connections. The Worker:

- spends at most 45 GitHub requests per call, counting token minting;
- reads at most 5 pages of issues and 10 pages of one comment thread;
- fetches comment threads 5 at a time;
- on running out, a read returns what it has with `truncated: true` and a `truncated_note`. This field exists only in the Worker. The Python server never truncates;
- before the first write, a write tool checks that the rest of the call (its remaining reads and writes, plus a margin for minting a token) fits in what is left of the 45. If it does not, it answers `budget_exhausted` and posts nothing, so a call is never left half done because of the limit. Ask again in a new request.

## Parity with the Python server

`test/parity/fixtures.json` is recorded from the real Python MCP server over fake data by `test/parity/record.py`. `test/parity.test.ts` replays the same data through the whole Worker and requires an identical envelope and identical text for every case.

The write tools are recorded as stories. Each story is a small dataset and an ordered list of tool calls against one mutable fake repository, and each step records the envelope and the exact list of GitHub calls the Python server made (reads and writes). The Worker has to answer the same and make the same calls in the same order, so a write the Python server would not make fails the test. A step can inject a GitHub failure, so partial-write behaviour is compared too. The tool list, descriptions, argument names and annotations are compared as well. `tests/test_worker_parity_fixtures.py` fails if the committed file no longer matches the Python server. To refresh after changing the Python rules:

```sh
python3 worker/test/parity/record.py
```

A step can also say that someone else edited the issue while the done gate was reading (`drift`). `release_task` with `status: done` decides the gate from one read of the issue and its thread. Just before it writes, it reads the issue again and compares its state, labels, `updated_at` and comment count with the first read. If they differ it answers `stale_plan` and posts nothing. This costs no extra GitHub call, because the re-read was already there. The Python server and the Worker both do it, and one story records the refusal and then the successful retry.

One known difference: JavaScript puts integer-like object keys (a section headed `### 2024`) first. Those cases are compared as parsed JSON, not as text.

## Local use

Node 22.12 or newer:

```sh
cd worker
npm ci
npm run typecheck
npm test
npm run dry-run   # builds the bundle; calls no Cloudflare API and deploys nothing
```

Do not add a deploy script, a route, a custom domain, a binding, a variable, or a secret here without the owner's approval. The Worker's Cloudflare build uploads a version and does not deploy it. See the hosting doc.

Builds for other branches, such as PR branches, run `npx wrangler versions check` from `/worker` with the watch path `worker/*`. That command validates the build and uploads nothing. The `previews` block in `wrangler.jsonc` is kept so `wrangler preview` also works.
