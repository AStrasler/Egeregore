# Egregore on Cloudflare Workers (read and write tools, not reachable)

This directory is the hosted Egregore MCP server, built up in small pull requests. The design is in [docs/cloudflare-hosting.md](../docs/cloudflare-hosting.md). The local Python server in `mcp/` stays the working server until the later pull requests land.

**Status: not deployed and closed.** Merging this directory uploads a Worker *version* (Workers Builds runs `versions upload`) and deploys nothing. Even when a version is deployed later, every coordination tool answers `unauthenticated`, because there is no caller authentication yet. That holds for the write tools too: until the bearer-token pull request lands, none of them can reach GitHub.

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

The write tools follow the Python server's order of reads, guards and writes step for step, so a refusal makes no write and a half-finished call leaves the same trace. Nothing merges, closes or approves anything. They are marked as writes (`readOnlyHint: false`, `destructiveHint: false`). With no caller identified they answer `unauthenticated` before any GitHub call, and with no GitHub App settings they answer `unconfigured`.

Every tool returns the same `{ok, result, error}` envelope as the Python server, as `structuredContent` and as indent-2 JSON text. Envelope errors do not set `isError`, as in Python.

## Layout

- `wrangler.jsonc`: Worker `egeregore`, `workers_dev: false`, `preview_urls: false`, no routes, no bindings, no vars, no secrets.
- `src/index.ts`: the Worker. Stateless `createMcpHandler` at `/mcp`. Builds a fresh service, GitHub client and subrequest budget per request. `resolveAgent` is the seam the auth pull request fills; today it returns `null`.
- `src/github-app.ts`: GitHub App auth with WebCrypto. RS256 JWT, PKCS#1 to PKCS#8 conversion, an installation token limited to the `Egeregore` repository with `issues:write`, `pull_requests:read`, `metadata:read`. The token is checked after it is minted and refused if it is broader. Cached per isolate.
- `src/github.ts`: the GitHub REST client. Pagination that cannot leave the API origin, no redirects (so the bearer token never goes to another host), secret scrubbing, and a subrequest budget.
- `src/coordination.ts`: the comment-marker and lease rules, ported from `scripts/coordination.py` and `mcp/egregore_mcp/comments.py`. `src/pycompat.ts` holds the Python-versus-JavaScript string details (`strip`, `splitlines`, `\s`, `\d`, `fromisoformat`).
- `src/service.ts`, `src/specialists.ts`, `src/server.ts`: the five read tools and the six write tools. `src/comments.ts` builds the claim, renewal, progress, release, handoff and conflict comments. `src/reviews.ts` reads the Codex review state of a pull request. `src/rules.ts` holds the field and done-gate rules that the write tools share.
- `test/`: Vitest tests that run inside the Workers runtime. No network, no credentials; RSA keys are generated at test time.

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
