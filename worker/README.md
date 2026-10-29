# Egregore on Cloudflare Workers (scaffold)

This directory is the start of the hosted Egregore MCP server. It is **not deployed** and exposes **one tool, `ping`**. The design is in [docs/cloudflare-hosting.md](../docs/cloudflare-hosting.md). The local Python server in `mcp/` stays the working server until the later pull requests land.

What the scaffold sets:

- `wrangler.jsonc`: Worker name `egeregore`, `workers_dev: false`, `preview_urls: false`, no routes, no bindings, no secrets.
- `src/index.ts`: `createMcpHandler` from the Agents SDK, stateless, at `/mcp`.
- `src/server.ts`: the MCP server factory. `ping` only.
- `test/`: Vitest tests that run inside the Workers runtime.

Local use needs Node 22.12 or newer:

```sh
cd worker
npm ci
npm run typecheck
npm test
npm run dry-run   # builds the bundle; calls no Cloudflare API and deploys nothing
```

Do not add a deploy script, a route, a custom domain, a binding, or a secret here without the owner's approval. Set the Worker's Cloudflare build to upload a version, not deploy. See the hosting doc.
