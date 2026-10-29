# Egregore on Cloudflare Workers (read-only core)

This directory is the hosted Egregore MCP server, built up in small pull requests. The design is in [docs/cloudflare-hosting.md](../docs/cloudflare-hosting.md). The local Python server in `mcp/` stays the working server until the later pull requests land.

**Status: not deployed, read-only, and closed.** Merging this directory uploads a Worker *version* (Workers Builds runs `versions upload`) and deploys nothing. Even when a version is deployed later, every coordination tool answers `unauthenticated`, because there is no caller authentication yet.

## Tools

| Tool | Reads | Matches Python |
| --- | --- | --- |
| `get_tasks` | issues, then each issue's comments (for the lease) | `get_tasks` |
| `get_task` | one issue and its comments | `get_task` |
| `get_task_history` | one issue's comment thread, classified | `get_task_history` |
| `get_owner_decisions` | Decision issues and their `Decision:` comments | `get_owner_decisions` |
| `get_specialists` | `mcp/specialists.json`, bundled at build time | `get_specialists` |
| `ping` | nothing (kept from the scaffold; safe to remove) | none |

There are no write tools. A write tool needs the OAuth layer first.

Every tool returns the same `{ok, result, error}` envelope as the Python server, as `structuredContent` and as indent-2 JSON text. Envelope errors do not set `isError`, as in Python.

## Layout

- `wrangler.jsonc`: Worker `egeregore`, `workers_dev: false`, `preview_urls: false`, no routes, no bindings, no vars, no secrets.
- `src/index.ts`: the Worker. Stateless `createMcpHandler` at `/mcp`. Builds a fresh service, GitHub client and subrequest budget per request. `resolveAgent` is the seam the auth pull request fills; today it returns `null`.
- `src/github-app.ts`: GitHub App auth with WebCrypto. RS256 JWT, PKCS#1 to PKCS#8 conversion, an installation token limited to the `Egeregore` repository with `issues:write`, `pull_requests:read`, `metadata:read`. The token is checked after it is minted and refused if it is broader. Cached per isolate.
- `src/github.ts`: the GitHub REST client. Pagination that cannot leave the API origin, no redirects (so the bearer token never goes to another host), secret scrubbing, and a subrequest budget.
- `src/coordination.ts`: the comment-marker and lease rules, ported from `scripts/coordination.py` and `mcp/egregore_mcp/comments.py`. `src/pycompat.ts` holds the Python-versus-JavaScript string details (`strip`, `splitlines`, `\s`, `\d`, `fromisoformat`).
- `src/service.ts`, `src/specialists.ts`, `src/server.ts`: the five read tools.
- `test/`: Vitest tests that run inside the Workers runtime. No network, no credentials; RSA keys are generated at test time.

## Free-plan limits

The Workers Free plan allows 50 subrequests and 10 ms of CPU per request, and 6 open connections. The Worker:

- spends at most 45 GitHub requests per call, counting token minting;
- reads at most 5 pages of issues and 10 pages of one comment thread;
- fetches comment threads 5 at a time;
- on running out, returns what it has with `truncated: true` and a `truncated_note`. This field exists only in the Worker. The Python server never truncates.

## Parity with the Python server

`test/parity/fixtures.json` is recorded from the real Python MCP server over fake data by `test/parity/record.py`. `test/parity.test.ts` replays the same data through the whole Worker and requires an identical envelope and identical text for every case. `tests/test_worker_parity_fixtures.py` fails if the committed file no longer matches the Python server. To refresh after changing the Python rules:

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

Builds for other branches, such as PR branches, run `npx wrangler versions check` from `/worker` with the watch path `worker/*`. That command validates the build and uploads nothing.
