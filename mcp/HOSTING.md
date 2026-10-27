# Hosting

v0.1 runs on the machine that owns the MCP client. Nothing in this pull request provisions a host, a secret, or a paid account.

## Now: local stdio

Cursor or Codex starts `python3 -m egregore_mcp` and speaks MCP over stdin and stdout. The process exits when the client disconnects. There is no always-on service and no bill.

Streamable HTTP is the same process, bound to `127.0.0.1`. It is there for a client that speaks HTTP on the same machine. It is not a public endpoint. The server refuses any other bind address.

The GitHub token stays in the client config on that machine.

## Cloudflare Workers free tier

Workers is the only hosted option worth writing down, and only on the free tier. This pull request does not create a Worker, a route, or an API token.

The server is a CPython program. It uses the MCP Python SDK's Streamable HTTP stack (Starlette and uvicorn) and the stdlib HTTP client. That stack does not run on the Workers runtime. A Worker would be a different program, plus a place to store `EGREGORE_GITHUB_TOKEN`. Creating that Worker, and storing that token, are owner decisions: a deploy and a credential.

The free tier also limits CPU time and is a poor fit for a process that stays up waiting on stdio. Local stdio does not hit that limit because nobody is hosting it.

Do not turn this into a paid Cloudflare plan, and do not add Vercel, Supabase, or another hosted backend. Those are out of scope for v0.1.

## What Aaron would have to approve later

1. A GitHub App, or one token per role, so comments stop sharing `@AStrasler`.
2. A Worker, or any other host, if local stdio is no longer enough.
3. The secret that host would use to call GitHub.

Until then, each client runs the server itself.
