# Hosting Egregore on Cloudflare Workers

Status: design only. This document deploys nothing, creates no secret, changes no Cloudflare setting, and adds no code. The working server is still the local Python server in `mcp/`.

Aaron approved starting the port on the Workers **Free** plan, in a Cloudflare account used only for Egregore. Two rules sit above everything below:

1. **Never charge anything.** No billing change, no paid plan, no custom domain, no paid feature. If the Free plan is too small, stop and ask Aaron. Do not upgrade to make a test pass.
2. **Aaron owns every release.** Nothing is promoted or enabled on `workers.dev` without Aaron's own words. A comment from his account is not proof he approved. Egregore may stage secrets on a new, undeployed version from its own box, because a staged version is not live. The procedure is [DEPLOY-RUNBOOK.md](DEPLOY-RUNBOOK.md).

## Decision

| Question | Choice |
| --- | --- |
| Language and runtime | TypeScript on the Workers runtime. The Python server's stack (CPython, Starlette, uvicorn, stdlib HTTP) does not run on Workers. |
| MCP framework | Cloudflare's Agents SDK, `createMcpHandler` from `agents/mcp/server`, with the MCP TypeScript SDK v2 (`@modelcontextprotocol/server`). |
| State | **Stateless.** The handler builds a fresh server for every request. No Durable Objects, no MCP session, no WebSocket, no KV, D1, or R2. |
| Where coordination state lives | Where it lives today: GitHub issues, labels, and comments. A lease is still a comment. The Worker stores nothing. |
| Identity to GitHub | The existing private GitHub App `egregore-coordinator`, with installation tokens scoped to `AStrasler/Egeregore` and the same permission ceiling as the Python server. |
| Caller authentication | Per-agent bearer tokens, stored hashed, with the agent identity bound to the token. OAuth later, if a client needs it. |
| Plan | Workers Free. |

Why stateless and no Durable Objects: the 11 tools only read and write GitHub. A claim race is already settled by GitHub's comment order, as the Phase 5 live tests showed, so the Worker needs no lock of its own. Durable Objects are on the Free plan but add a billing surface and a state store to secure. They can be reconsidered later if a real need appears. Do not add one now.

Why not the alternatives:

- **Python Workers.** Still limited in packages and startup behavior, and the MCP Python SDK stack does not run there. It would be a rewrite anyway, with less support.
- **`McpAgent` (Durable Object per session).** Deprecated in favor of `createMcpHandler`, and it needs Durable Objects.
- **Another host (Vercel, Supabase, a VPS).** Out of scope. Free tier only, Cloudflare only.
- **Stay local.** Works, but a client can use Egregore only while a machine that holds the key is online. That is what blocked the first ChatGPT smoke test.

## Request flow

```
client (ChatGPT, Codex, Grokbot, Cursor)
  |  HTTPS  POST /mcp   Authorization: Bearer <agent token>
  v
Worker `egeregore`
  1. Reject non-/mcp paths and bad Origin or Host. Cheap, before parsing the body.
  2. Hash the bearer token, look it up in the token table, and bind the agent, permissions, repositories and tools of the matching record. No match leaves the request with no caller.
  3. createMcpHandler builds a fresh MCP server for this request. Every tool call asks the access gate first: no caller is `unauthenticated`, a caller without the permission, tool or repository is `forbidden`.
  4. A tool call mints (or reuses, within this isolate) an installation token for Egeregore only.
  5. The tool reads or writes GitHub issues as egregore-coordinator[bot].
  v
GitHub API (AStrasler/Egeregore issues)
```

The tool rules (lease length, `wrong_role`, `lease_conflict`, `lease_expired`, `not_claimable`, no merge and no approval tool) are ported from `mcp/egregore_mcp` and keep their names and behavior. The Python tests are the specification. The TypeScript port must pass equivalent tests.

## Threat model

Assets: the GitHub App private key, the agent tokens, the integrity of task state in GitHub issues, and Aaron's approval authority. Not stored here: health data, personal data, or WHOOP Lens or Weather Intelligence data. This server never touches those projects.

| # | Threat | Control | Residual risk |
| --- | --- | --- | --- |
| 1 | An unauthenticated caller reaches the Worker. | Every tool needs a valid bearer token. Without one it answers `unauthenticated` in the tool envelope, makes no GitHub call, and says nothing about which part failed. `initialize` and `tools/list` return only fixed server metadata. `workers_dev` and `preview_urls` stay off until the owner turns them on. No route, no custom domain. | A leaked URL plus a stolen token. See 2. |
| 2 | An agent token is stolen. | Tokens are 256 bits of randomness, shown once, and stored only as SHA-256 hashes. Each token is its own record, so one can be revoked alone, and each record limits the permissions, tools and repositories it may use. A token can do only what the MCP tools allow: write issue comments and labels on `Egeregore`. It cannot merge, deploy, change settings, or approve. | The holder can act as that agent until the hash is removed. Rotation is a secret update by Aaron. |
| 3 | An agent pretends to be another agent or role. | The agent identity comes from the token record. No tool takes an `agent` argument, and an extra one is ignored. A token may act only as the one agent in its record, and never as `aaron`. | None beyond 2. |
| 4 | An agent approves its own work. | There is no merge or approve tool. `Decision:` comments are honored only from Aaron's own GitHub account by the existing issue-hygiene workflow. A comment by `egregore-coordinator[bot]` is never approval. | Aaron pasting a decision for an agent. His call. |
| 5 | The GitHub App private key leaks. | The key is a Worker secret. Egregore stages it from its box onto an undeployed version with `wrangler versions secret bulk`, never `wrangler secret put`, which deploys at once. It goes in on standard input and is never an argument. Aaron's sign-off promotes the version. It is never in the repo, a log, a tool result, or an error message. The Worker requests tokens with `repositories: ["Egeregore"]` and the permissions `issues:write`, `pull_requests:read`, `metadata:read` only, and checks the response, as the Python server does. | The key itself is not limited to one repository. The app is installed on all of Aaron's repositories, so whoever holds the key can mint a token for any of them, with Issues write and Pull requests read. Narrowing the installation to `Egeregore` is Aaron's decision. This design does not assume it. |
| 6 | Prompt injection: text in an issue tells an agent to do something unsafe. | Tool results are data, not instructions. The server has no merge, deploy, settings, or approval tool, so the worst an injected agent can do is write issue comments. The existing `AGENTS.md` rules still apply to each agent. | Misleading comments in a public repo. Reviewable and reversible. |
| 7 | A browser page calls the server (DNS rebinding, cross-site request). | The handler validates `Origin` and `Host`. CORS is off unless a client needs it. Bearer tokens are not cookies, so a browser does not attach them on its own. | None expected. |
| 8 | Secrets reach logs or errors. | Observability and logpush stay off. The code never logs headers, token values, or request bodies. Errors return a short code, not upstream text that could echo a token. | A future change that logs. Review catches it. |
| 9 | Supply chain: a bad package or action. | Exact versions in `package.json`, a committed lockfile, `npm ci`, and GitHub Actions pinned by full commit SHA. | Transitive packages. Dependabot is not enabled here. Aaron decides that. |
| 10 | Accidental deploy or promotion. | Builds only upload versions. See "Build configuration". `wrangler.jsonc` has no route and no bindings. Aaron promotes by hand. | A Cloudflare setting changed in the dashboard. |
| 11 | Build credentials too broad. | Use a Cloudflare API token limited to Workers Scripts edit and Account Settings read, on the Egregore account only. Do not let Workers Builds create its own broad token. | The current token Aaron gave the coordinator is broader than needed. Replace it. |
| 12 | Denial of service or quota exhaustion. | Free plan: 100,000 requests per day, then Cloudflare returns errors. It does not bill. A request with no valid token makes no GitHub call, so junk requests are cheap. | An attacker with the URL can use up the day's quota. That is an outage, not a charge. |

## Authentication

### Per-agent bearer tokens (first release)

- Each token is 32 random bytes, base64url, made on Egregore's box with `worker/scripts/generate-agent-token.mjs` (the runbook runs it through `provision-agent-tokens.mjs`), and kept only in an owner-only file outside the repository. Aaron hands it to that agent's client without passing it through chat. It is never committed. An agent may hold several tokens at once, so a rotation has no gap.
- The Worker holds a secret `EGREGORE_AGENT_TOKENS`: a JSON array with one record per token. Only SHA-256 hashes are stored. A high-entropy random token does not need a slow hash, and a slow one would burn the 10 ms CPU budget. One SHA-256 per request is a few microseconds.
- A record has these fields, and no others:

  | Field | Meaning |
  | --- | --- |
  | `sha256` | The 64 hex characters of the SHA-256 of the token. Required, and unique in the array. |
  | `agent` | `chatgpt`, `grokbot`, `cursor` or `codex`. The identity the token carries. `aaron` is not allowed. |
  | `permissions` | A non-empty list from `read`, `write`, `review`. |
  | `repos` | One to 16 explicit `owner/name` entries. No wildcard. Compared case-insensitively. |
  | `tools` | `["*"]`, or a list of tool names from this server. |
  | `label` | Optional note for people, up to 80 characters. Not used by the Worker. |

- Permissions do not imply one another. `read` covers `ping`, `get_tasks`, `get_task`, `get_task_history`, `get_owner_decisions` and `get_specialists`. `write` covers `claim_task`, `renew_lease`, `release_task`, `post_progress` and `handoff_task`. `review` covers `request_review` alone, because it makes Codex run.
- The whole array is rejected, and every tool answers `unauthenticated`, when it is unset, is not JSON, is over 16 KiB or 32 records, or has any record that breaks these rules. A typo can never leave one token working with more access than intended. The Worker does not say why. Run `worker/scripts/check-agent-tokens.mjs` before setting the secret to see the reasons.
- On each request the Worker reads one `Authorization: Bearer` header of 32 to 256 characters, hashes it once, and compares the digest to every stored digest with a comparison that does not stop early. The matching record is the caller. The agent, and so every `Claim-Agent` and `Agent:` line the tools write, comes from that record. This replaces the Python server's `EGREGORE_AGENT` environment variable, which only works because each process has one agent.
- Each tool call is checked in a fixed order: identity (`unauthenticated`), the tool allow-list, the permission, then the repository allow-list against the configured `EGREGORE_REPO` (`forbidden`). A tool with no permission mapped is refused. All of that runs before any GitHub call, so a refused call spends no subrequest. `EGREGORE_REPO` is also checked as a plain `owner/name`: empty, `.` and `..` segments, backslashes and extra slashes are refused.
- This deviates from the first draft of this section, which had the Worker answer HTTP 401 before reading the body. The Worker now answers in the tool envelope, as the Python server does, so a client sees one error shape. The cost is that the body is parsed before the refusal. Parsing is cheap and `initialize` and `tools/list` expose only fixed metadata.
- Revoke by removing one record and updating the secret. Rotate by adding a record, switching the client, then removing the old one.
- The 5 KB limit on a Free-plan variable is far above what a few records need. The Worker accepts up to 16 KiB.
- Setup steps are in [DEPLOY-RUNBOOK.md](DEPLOY-RUNBOOK.md) and `worker/README.md`. The model comes from egregore-labs/egregore at d570d10, rewritten here. See `THIRD_PARTY_NOTICES.md`.

### OAuth (later, only if needed)

Some hosted clients, ChatGPT among them, may only connect to a remote MCP server through OAuth. If so, add Cloudflare's `@cloudflare/workers-oauth-provider` with GitHub as the identity provider and an allowlist of GitHub accounts. That is PR 6 in the sequence below. It needs an OAuth app and KV for grants, so it is a separate decision for Aaron. Until then, bearer tokens are the only way in.

## GitHub App key as a Worker secret

- Secrets: `EGREGORE_APP_PRIVATE_KEY` and `EGREGORE_AGENT_TOKENS`. Plain variables (not secret): `EGREGORE_APP_ID` (`5503558`), `EGREGORE_REPO` (`AStrasler/Egeregore`).
- **Egregore stages the secrets from its own box, and Aaron approves the release.** `wrangler versions secret bulk` puts them on a new version that is not live. Promoting that version is Aaron's gate. Nothing prints a secret, and nothing sets one without the runbook's checks. `EGREGORE_APP_ID` is a number, not sensitive, but the runbook sets it with the secrets, because `wrangler.jsonc` carries no variables and a secret survives later uploads.
- The Worker signs a short RS256 JWT with WebCrypto, then calls `POST /app/installations/{id}/access_tokens` with `repositories: ["Egeregore"]` and the three permissions. It refuses to proceed if the response lists another repository or a wider permission. This is the same check as `mcp/egregore_mcp`.
- WebCrypto imports PKCS#8 keys. GitHub gives a PKCS#1 (`BEGIN RSA PRIVATE KEY`) key. Either convert the key once to PKCS#8 before storing it, or wrap it at runtime. The code, not this document, will pick one.
- An installation token lasts one hour. The Worker may keep it in memory inside one isolate to save a subrequest. That is a cache, not state of record. A cold isolate mints a new one.
- The token is never returned to a client and never logged.

## Free-tier limits and risks

From Cloudflare's [limits](https://developers.cloudflare.com/workers/platform/limits/) page, checked 2026-10-29:

| Limit (Workers Free) | Value | What it means here |
| --- | --- | --- |
| Requests | 100,000 per day, resets 00:00 UTC | Far above expected use. Also the ceiling for abuse (threat 12). |
| CPU time per request | **10 ms** | The main risk. Waiting on GitHub does not count, but JSON parsing, zod validation, JWT signing, and SHA-256 do. |
| Subrequests per request | **50** external | `claim_task` and `get_task_history` read several pages of comments plus the token mint. Page sizes and counts must be capped. |
| Memory | 128 MB | Not a concern. |
| Worker startup | 1 second | A small bundle. The scaffold is about 1.2 MiB, 212 KiB gzipped. |
| Environment variable size | 5 KB | Enough for the agent table. |

Risks and how to keep them small:

- **10 ms CPU.** Measure CPU time per tool early, from PR 3 onward, using the real runtime and the dashboard's CPU metrics. Keep the RS256 signature to one per isolate hour. Avoid heavy per-request work. If a tool cannot fit in 10 ms, the options are to trim it or to ask Aaron about the Workers Paid plan, about $5 a month. That is his decision. The default answer is no.
- **50 subrequests.** Each tool gets a fixed budget (token mint, then a bounded number of GitHub calls). A tool that would exceed it returns a clear "too large" error instead of failing midway through a write. Writes check the budget before the first write.
- **GitHub rate limits.** Installation tokens get 5,000 requests per hour, shared by all agents. Plenty at this scale.
- **Exceeding Free limits fails, it does not bill.** Do not attach a payment method, add a paid add-on, or turn on any usage-billed product. Durable Objects, Queues, R2, and Logpush are all out.
- **Platform drift.** The Agents SDK and MCP SDK v2 are young. Versions are pinned exactly. `agents@0.31.0` requires `@modelcontextprotocol/server@2.3.1` exactly.

## Build configuration: no auto-deploy

The `egeregore` Worker in the Egregore Cloudflare account is connected to this repository. Cloudflare's Workers Builds runs on every push to the branch you pick. Its default deploy command is `npx wrangler deploy`, which publishes immediately. Do not use that. Set the following before the scaffold lands on `main`:

| Setting (Worker, Settings, Build) | Value |
| --- | --- |
| Root directory | `worker` |
| Build command | empty (the dry run in CI covers the build) |
| Deploy command | `npx wrangler versions upload` |
| Non-production branch builds | on, with preview command `npx wrangler versions upload` |
| API token | A token Aaron makes: Workers Scripts edit and Account Settings read, this account only. Not an auto-created one, which also gets KV, R2, and routes access. |

`wrangler versions upload` creates a version without making it live. With `workers_dev: false` and `preview_urls: false` in `wrangler.jsonc`, the version gets no public URL. Releasing is a separate act, run only by Aaron or on his explicit say-so: `npx wrangler versions deploy` or the dashboard's Deployments page.

If the build settings are not changed first, the first build after the scaffold merges could run `wrangler deploy` and replace the starter "Hello world" with the scaffold. With `workers_dev` off that Worker would still have no public URL, but it would be a deploy nobody approved. Change the settings first.

CI in this repository is separate. The `worker` job in `.github/workflows/ci.yml` runs only when `worker/**` changes. It installs, type-checks, tests, and runs `wrangler deploy --dry-run`, which builds the bundle locally and calls no Cloudflare API. It holds no secret and no Cloudflare token.

## The seven pull requests

Each is small, reviewed by Codex, and merged only by Aaron. A merge deploys nothing, because of the build settings above.

| PR | Contents | Gate |
| --- | --- | --- |
| 1 | This design document. | Aaron reads and merges. |
| 2 | `worker/` scaffold: pinned `package.json`, `wrangler.jsonc` (name `egeregore`, `workers_dev` false, `preview_urls` false, no bindings), a `ping` tool, Vitest tests, and the `worker` CI job. | Change the Cloudflare build settings first. |
| 3 | The read-only tools in TypeScript: `get_tasks`, `get_task`, `get_task_history`, `get_owner_decisions`, `get_specialists`. A GitHub client with the App-token mint and its scope checks. Fake-GitHub tests. CPU and subrequest budgets measured. | Not reachable: no auth yet. |
| 4 | The write tools: `claim_task`, `renew_lease`, `post_progress`, `handoff_task`, `request_review`, `release_task`. All the Python rules and tests ported, including `lease_conflict`, `lease_expired`, `wrong_role`, `not_claimable`, and the no-merge boundary. Delivered as two pull requests: 4a ports the six write tools and records them against the Python server as parity stories; 4b adds the handoff lifecycle rules and the scan-then-apply stale-plan check. The lifecycle rules run in the daily lease-watch workflow, which stays Python in GitHub Actions and is not moved into the Worker. The Worker gets the stale-plan check on the done gate (`stale_plan`). | Not reachable: no auth yet. |
| 5 | Per-agent hashed bearer tokens and a fail-closed permission model (`read`, `write`, `review`, a tool allow-list and a repository allow-list). Identity from the token, no `agent` argument. Tests for missing, wrong, cross-agent and under-privileged tokens. A local script makes tokens and checks the config. With no secret set, every tool still answers `unauthenticated`. | Required before anything is reachable. |
| 6 | Optional OAuth with GitHub login, only if a client needs it. | A separate decision by Aaron. |
| 7 | Go live, following [DEPLOY-RUNBOOK.md](DEPLOY-RUNBOOK.md): Egregore stages the secrets from its box, Aaron approves the release, a smoke test (`worker/scripts/smoke-test.mjs`) runs against the Worker, Aaron hands each token to its client. Cleanup. | Aaron's explicit approval. |

Nothing is promoted or given a public URL before PR 5 is merged and Aaron approves PR 7.

## Not covered by this document

- Creating or changing any Cloudflare resource, domain, route, binding, or secret.
- Narrowing the GitHub App installation, or changing its permissions.
- Any change to WHOOP Lens or Weather Intelligence.
- Any paid plan. If the Free plan proves too small, report it and stop.
