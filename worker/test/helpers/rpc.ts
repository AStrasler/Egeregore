import { createWorker, type Deps, type Env } from "../../src/index";

export const HEADERS = {
  "content-type": "application/json",
  accept: "application/json, text/event-stream",
};

export const CTX = { waitUntil() {}, passThroughOnException() {} } as unknown as ExecutionContext;

/** The response is JSON or a one-event SSE stream. Return the JSON-RPC message. */
export async function message(res: Response): Promise<any> {
  const text = await res.text();
  if ((res.headers.get("content-type") ?? "").includes("text/event-stream")) {
    const data = text.split("\n").find((line) => line.startsWith("data:"));
    return JSON.parse((data ?? "").slice(5).trim());
  }
  return JSON.parse(text);
}

export type TestWorker = ReturnType<typeof createWorker>;

export function client(worker: TestWorker, env: Env, agent: string | null = null) {
  let id = 0;
  const call = async (method: string, params: unknown = {}): Promise<any> => {
    id += 1;
    const headers: Record<string, string> = { ...HEADERS };
    if (agent !== null) headers["x-test-agent"] = agent;
    const request = new Request("https://example.invalid/mcp", {
      method: "POST",
      headers,
      body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
    });
    const res = await worker.fetch(request as Parameters<TestWorker["fetch"]>[0], env, CTX);
    return message(res);
  };
  return {
    call,
    list: async () => (await call("tools/list")).result.tools as any[],
    tool: async (name: string, args: Record<string, unknown> = {}) =>
      (await call("tools/call", { name, arguments: args })).result as any,
  };
}

export const agentFromHeader: Deps["resolveAgent"] = (request) => request.headers.get("x-test-agent");
