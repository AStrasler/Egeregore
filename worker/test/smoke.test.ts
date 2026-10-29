import { beforeAll, describe, expect, it } from "vitest";
import { createWorker } from "../src/index";
import { parseReply, runSmoke, type Post, type TokenEntry } from "../scripts/smoke-lib";
import { API, FakeGitHub, REPO } from "./helpers/fake-github";
import { testKey, type TestKey } from "./helpers/keys";
import { CTX } from "./helpers/rpc";
import { randomToken, recordFor } from "./helpers/tokens";

const NOW = Date.parse("2026-10-27T18:00:00Z");
const SHA = "a".repeat(40);
const dataset = () => ({
  issues: [
    {
      number: 1,
      title: "[Task]: smoke",
      state: "open",
      labels: [{ name: "type:task" }, { name: "status:ready" }],
      body: "### Role\n\nany\n",
      html_url: `https://github.com/${REPO}/issues/1`,
    },
    { number: 2, title: "pull", state: "open", labels: [], body: "", html_url: `https://github.com/${REPO}/pull/2` },
  ],
  comments: { "1": [] as any[], "2": [] as any[] },
  pulls: { "2": { number: 2, state: "open", merged: false, head: { sha: SHA } } },
});

let key: TestKey;
beforeAll(async () => {
  key = await testKey();
});

interface Plan {
  agent: string;
  permissions: string[];
  tools?: string[];
}

/** The real Worker, real bearer auth, a fake GitHub. Tokens are made fresh each run. */
async function setup(plans: Plan[]) {
  const github = new FakeGitHub({ dataset: dataset(), now: () => NOW, publicKey: key.publicKey });
  const worker = createWorker({ fetch: github.fetch, now: () => NOW, api: API });
  const entries: TokenEntry[] = [];
  const records: Record<string, unknown>[] = [];
  for (const plan of plans) {
    const token = randomToken();
    const tools = plan.tools ?? ["*"];
    entries.push({ agent: plan.agent, permissions: plan.permissions, tools, token });
    records.push(await recordFor(token, { agent: plan.agent, permissions: plan.permissions as any, tools }));
  }
  const env = {
    EGREGORE_APP_ID: "123",
    EGREGORE_APP_INSTALLATION_ID: "42",
    EGREGORE_APP_PRIVATE_KEY: key.pkcs8Pem,
    EGREGORE_AGENT_TOKENS: JSON.stringify(records),
  };
  const post: Post = async (headers, body) => {
    const request = new Request("https://example.invalid/mcp", { method: "POST", headers, body: JSON.stringify(body) });
    const res = await worker.fetch(request as any, env, CTX);
    return parseReply(res.status, res.headers.get("content-type") ?? "", await res.text());
  };
  return { github, entries, post, env };
}

const failures = (report: Awaited<ReturnType<typeof runSmoke>>) => report.checks.filter((check) => check.status === "fail");

describe("smoke test against the real Worker with a fake GitHub", () => {
  it("passes for a read-only token, a writer and a reviewer, and leaves the issue ready", async () => {
    const { github, entries, post } = await setup([
      { agent: "chatgpt", permissions: ["read"] },
      { agent: "cursor", permissions: ["read", "write", "review"] },
      { agent: "codex", permissions: ["read", "write"] },
    ]);
    const report = await runSmoke({ post, tokens: entries, issue: 1, pull: 2 });
    expect(failures(report)).toEqual([]);
    expect(report.ok).toBe(true);
    expect(report.passed).toBeGreaterThan(60);
    // chatgpt's refusals posted nothing: the only comments come from cursor and codex.
    const writers = github.writes.map((call) => call.method + " " + call.path.replace(`/repos/${REPO}/`, ""));
    expect(writers.filter((line) => line.startsWith("POST issues/1/comments")).length).toBe(8);
    expect(writers.filter((line) => line === "POST issues/2/comments")).toHaveLength(1);
    expect(report.cleanup.join(" ")).toContain("Close test issue #1");
    const issue = github.dataset.issues.find((item) => item.number === 1)!;
    expect(issue.labels.map((label: any) => label.name)).toContain("status:ready");
  });

  it("asks for a review only once per head commit", async () => {
    const { github, entries, post } = await setup([
      { agent: "grokbot", permissions: ["read", "write", "review"] },
      { agent: "cursor", permissions: ["read", "write", "review"] },
    ]);
    const report = await runSmoke({ post, tokens: entries, issue: 1, pull: 2 });
    expect(failures(report)).toEqual([]);
    expect(github.writes.filter((call) => call.path.endsWith("/issues/2/comments"))).toHaveLength(1);
    expect(report.checks.some((check) => check.status === "skip" && check.name.startsWith("cursor"))).toBe(true);
  });

  it("makes no write with --no-write or without a pull number", async () => {
    const { github, entries, post } = await setup([{ agent: "cursor", permissions: ["read", "write", "review"] }]);
    const report = await runSmoke({ post, tokens: entries, issue: 1, write: false });
    expect(failures(report)).toEqual([]);
    expect(github.writes).toEqual([]);
    expect(report.skipped).toBeGreaterThan(0);
    expect(report.cleanup).toEqual([]);
  });

  it("honours a token's tool list: tools outside it are forbidden, and the write flow is skipped", async () => {
    const { github, entries, post } = await setup([{ agent: "cursor", permissions: ["read", "write"], tools: ["ping", "get_tasks", "claim_task"] }]);
    const report = await runSmoke({ post, tokens: entries, issue: 1 });
    expect(failures(report)).toEqual([]);
    expect(report.checks.some((check) => check.name.includes("get_task is forbidden") && check.status === "pass")).toBe(true);
    expect(report.checks.some((check) => check.status === "skip" && check.name.includes("write flow"))).toBe(true);
    expect(github.writes).toEqual([]);
  });

  it("fails loudly, and undoes a claim, when a token can do more than its record says", async () => {
    const { github, entries, post } = await setup([{ agent: "cursor", permissions: ["read", "write"] }]);
    const understated: TokenEntry[] = [{ ...entries[0], permissions: ["read"] }];
    const report = await runSmoke({ post, tokens: understated, issue: 1 });
    expect(report.ok).toBe(false);
    expect(failures(report).some((check) => check.name.includes("claim_task is forbidden") && (check.detail ?? "").includes("SUCCEEDED"))).toBe(true);
    expect(report.cleanup.join(" ")).toContain("should have been refused");
    const released = github.writes.some((call) => call.path.endsWith("/issues/1/comments") && JSON.stringify(call.body).includes("Lease-Release"));
    expect(released).toBe(true);
  });

  it("fails when a token is not in the configured records", async () => {
    const { entries, post } = await setup([{ agent: "cursor", permissions: ["read", "write"] }]);
    const report = await runSmoke({ post, tokens: [{ ...entries[0], token: randomToken() }], issue: 1 });
    expect(report.ok).toBe(false);
    expect(failures(report).some((check) => check.name.includes("ping answers pong"))).toBe(true);
  });

  it("releases the lease and reports a failure when a step breaks", async () => {
    const { github, entries, post } = await setup([{ agent: "cursor", permissions: ["read", "write"] }]);
    // Make post_progress fail by dropping the transport for that one tool.
    const flaky: Post = async (headers, body: any) => {
      if (body?.params?.name === "post_progress" && headers.authorization) throw new Error(`boom ${headers.authorization}`);
      return post(headers, body);
    };
    const report = await runSmoke({ post: flaky, tokens: entries, issue: 1 });
    expect(report.ok).toBe(false);
    expect(failures(report).map((check) => check.name).join("\n")).toContain("tools/call request");
    const released = github.writes.some((call) => call.path.endsWith("/issues/1/comments") && JSON.stringify(call.body).includes("Lease-Release"));
    expect(released).toBe(true);
  });

  it("never puts a token in a check name or detail", async () => {
    const { entries, post } = await setup([{ agent: "cursor", permissions: ["read"] }]);
    const echo: Post = async (headers, body: any) => {
      if (body?.params?.name === "get_tasks" && headers.authorization) throw new Error(`failed with ${headers.authorization}`);
      return post(headers, body);
    };
    const report = await runSmoke({ post: echo, tokens: entries, issue: 1 });
    const text = JSON.stringify(report);
    for (const entry of entries) expect(text).not.toContain(entry.token);
    expect(text).toContain("[token]");
  });
});

describe("parseReply", () => {
  it("reads a JSON body, a one-event stream, plain-text ping and a JSON-RPC error", () => {
    const envelope = { ok: true, result: { a: 1 } };
    const tool = { jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: JSON.stringify(envelope) }], structuredContent: envelope } };
    expect(parseReply(200, "application/json", JSON.stringify(tool)).envelope).toEqual(envelope);
    expect(parseReply(200, "text/event-stream", `event: message\ndata: ${JSON.stringify(tool)}\n\n`).envelope).toEqual(envelope);
    const ping = { jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: "pong" }] } };
    const reply = parseReply(200, "application/json", JSON.stringify(ping));
    expect(reply.text).toBe("pong");
    expect(reply.envelope).toBeNull();
    expect(parseReply(200, "application/json", JSON.stringify({ jsonrpc: "2.0", id: 1, error: { code: -1, message: "nope" } })).rpcError).toBe("nope");
    expect(parseReply(502, "text/html", "<html>").envelope).toBeNull();
  });
});
