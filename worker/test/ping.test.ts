import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { bearer, randomToken, recordFor } from "./helpers/tokens";

const HEADERS = {
  "content-type": "application/json",
  accept: "application/json, text/event-stream",
};

// The default worker, as deployed: the bearer path against a secret of hashed tokens.
const TOKEN = randomToken();
const ENV = { EGREGORE_AGENT_TOKENS: "" };

async function rpc(body: unknown, init: RequestInit = {}, token: string | null = TOKEN): Promise<Response> {
  ENV.EGREGORE_AGENT_TOKENS = JSON.stringify([await recordFor(TOKEN)]);
  const request = new Request("https://example.invalid/mcp", {
    method: "POST",
    headers: { ...HEADERS, ...(token === null ? {} : bearer(token)) },
    body: JSON.stringify(body),
    ...init,
  });
  const ctx = { waitUntil() {}, passThroughOnException() {} } as unknown as ExecutionContext;
  return worker.fetch(request as Parameters<typeof worker.fetch>[0], ENV, ctx);
}

/** The response is JSON or a one-event SSE stream. Return the JSON-RPC message. */
async function message(res: Response): Promise<any> {
  const text = await res.text();
  if ((res.headers.get("content-type") ?? "").includes("text/event-stream")) {
    const data = text.split("\n").find((line) => line.startsWith("data:"));
    return JSON.parse((data ?? "").slice(5).trim());
  }
  return JSON.parse(text);
}

const INIT = {
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "vitest", version: "0.0.0" },
  },
};

describe("egregore worker ping and transport", () => {
  it("initializes and names itself egregore", async () => {
    const res = await rpc(INIT);
    expect(res.status).toBe(200);
    const msg = await message(res);
    expect(msg.result.serverInfo.name).toBe("egregore");
  });

  it("lists ping, the five read tools and the six write tools", async () => {
    const res = await rpc({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
    expect(res.status).toBe(200);
    const msg = await message(res);
    expect(msg.result.tools.map((t: { name: string }) => t.name).sort()).toEqual(
      [
        "claim_task",
        "get_owner_decisions",
        "get_specialists",
        "get_task",
        "get_task_history",
        "get_tasks",
        "handoff_task",
        "ping",
        "post_progress",
        "release_task",
        "renew_lease",
        "request_review",
      ],
    );
  });

  it("answers ping with pong", async () => {
    const res = await rpc({
      jsonrpc: "2.0",
      id: 3,
      method: "tools/call",
      params: { name: "ping", arguments: {} },
    });
    const msg = await message(res);
    expect(msg.result.content[0].text).toBe("pong");
  });

  it("refuses ping without a valid token, as every other tool does", async () => {
    const call = { jsonrpc: "2.0", id: 6, method: "tools/call", params: { name: "ping", arguments: {} } };
    for (const token of [null, randomToken()]) {
      const msg = await message(await rpc(call, {}, token));
      expect(msg.result.structuredContent.error.code).toBe("unauthenticated");
    }
  });

  it("echoes a short message", async () => {
    const res = await rpc({
      jsonrpc: "2.0",
      id: 4,
      method: "tools/call",
      params: { name: "ping", arguments: { message: "hi" } },
    });
    const msg = await message(res);
    expect(msg.result.content[0].text).toBe("pong: hi");
  });

  it("rejects an over-long message", async () => {
    const res = await rpc({
      jsonrpc: "2.0",
      id: 5,
      method: "tools/call",
      params: { name: "ping", arguments: { message: "x".repeat(65) } },
    });
    const msg = await message(res);
    expect(msg.error !== undefined || msg.result?.isError === true).toBe(true);
  });

  it("does not serve other paths", async () => {
    const ctx = { waitUntil() {}, passThroughOnException() {} } as unknown as ExecutionContext;
    const res = await worker.fetch(new Request("https://example.invalid/other") as Parameters<typeof worker.fetch>[0], {}, ctx);
    expect(res.status).toBe(404);
  });

  it("rejects a hostile browser Origin", async () => {
    const res = await rpc(INIT, {
      headers: { ...HEADERS, origin: "https://evil.example" },
    });
    expect(res.status).toBe(403);
  });
});
