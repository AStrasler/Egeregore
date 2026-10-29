import { beforeAll, describe, expect, it } from "vitest";
import { createWorker } from "../src/index";
import { API, FakeGitHub, REPO } from "./helpers/fake-github";
import { testKey, type TestKey } from "./helpers/keys";
import { agentFromHeader, client, CTX } from "./helpers/rpc";

const READ_TOOLS = ["get_owner_decisions", "get_specialists", "get_task", "get_task_history", "get_tasks"];
const NOW = Date.parse("2026-10-27T18:00:00Z");

let key: TestKey;
beforeAll(async () => {
  key = await testKey();
});

function env(extra: Record<string, string> = {}) {
  return { EGREGORE_APP_ID: "123", EGREGORE_APP_INSTALLATION_ID: "42", EGREGORE_APP_PRIVATE_KEY: key.pkcs8Pem, ...extra };
}

describe("MCP surface", () => {
  it("lists ping and the five read tools, and no write tool", async () => {
    const worker = createWorker();
    const tools = await client(worker, {}).list();
    expect(tools.map((tool) => tool.name).sort()).toEqual(["ping", ...READ_TOOLS].sort());
    for (const tool of tools.filter((item) => item.name !== "ping")) {
      expect(tool.name).not.toMatch(/^(create|update|claim|release|renew|comment|add|remove|close|reopen|set|delete|post|write)/);
      expect(tool.annotations?.readOnlyHint, tool.name).toBe(true);
      expect(tool.annotations?.destructiveHint, tool.name).toBe(false);
    }
  });

  it("describes every read tool and gives it an input schema", async () => {
    const tools = await client(createWorker(), {}).list();
    for (const name of READ_TOOLS) {
      const tool = tools.find((item) => item.name === name);
      expect(tool.description.length).toBeGreaterThan(40);
      expect(tool.inputSchema.type).toBe("object");
      expect(tool.outputSchema).toBeDefined();
    }
  });

  it("answers `unauthenticated` to every read tool by default, whatever the settings", async () => {
    const github = new FakeGitHub({ dataset: { issues: [], comments: {} }, publicKey: key.publicKey });
    const worker = createWorker({ fetch: github.fetch, now: () => NOW, api: API });
    const caller = client(worker, env());
    for (const name of READ_TOOLS) {
      const args = name === "get_task" || name === "get_task_history" ? { issue_number: 1 } : {};
      const result = await caller.tool(name, args);
      expect(result.structuredContent.error.code, name).toBe("unauthenticated");
      expect(result.isError ?? false).toBe(false);
    }
    expect(github.calls).toHaveLength(0);
  });

  it("answers `unconfigured` with the missing names when no GitHub App is set", async () => {
    const worker = createWorker({ resolveAgent: agentFromHeader });
    const result = await client(worker, {}, "cursor").tool("get_tasks");
    expect(result.structuredContent.error.code).toBe("unconfigured");
    expect(result.structuredContent.error.details.missing).toEqual(["EGREGORE_APP_ID", "EGREGORE_APP_PRIVATE_KEY"]);
  });

  it("flags a malformed repository setting instead of using it", async () => {
    const worker = createWorker({ resolveAgent: agentFromHeader });
    const result = await client(worker, env({ EGREGORE_REPO: "not a repo" }), "cursor").tool("get_tasks");
    expect(result.structuredContent.error.details.missing).toEqual(["EGREGORE_REPO"]);
  });

  it("reads through the GitHub App with a repository-scoped token and no write call", async () => {
    const dataset = {
      issues: [{ number: 1, title: "[Task]: one", state: "open", labels: [{ name: "type:task" }], body: "### Role\n\ncursor\n", html_url: `https://github.com/${REPO}/issues/1` }],
      comments: { "1": [] },
    };
    const github = new FakeGitHub({ dataset, now: () => NOW, publicKey: key.publicKey });
    const worker = createWorker({ fetch: github.fetch, now: () => NOW, resolveAgent: agentFromHeader, api: API });
    const result = await client(worker, env(), "cursor").tool("get_tasks");
    expect(result.structuredContent.ok).toBe(true);
    expect(result.structuredContent.result.count).toBe(1);
    expect(github.calls.filter((call) => call.method !== "GET").map((call) => call.path)).toEqual([
      "/app/installations/42/access_tokens",
    ]);
    expect(github.calls[0].body).toEqual({
      repositories: ["Egeregore"],
      permissions: { issues: "write", pull_requests: "read", metadata: "read" },
    });
  });

  it("never puts the private key or a token in a response", async () => {
    const github = new FakeGitHub({ dataset: { issues: [], comments: {} }, now: () => NOW, publicKey: key.publicKey, failWith: { status: 500, times: 9, match: /issues$/ } });
    const worker = createWorker({ fetch: github.fetch, now: () => NOW, resolveAgent: agentFromHeader, api: API });
    const result = await client(worker, env(), "cursor").tool("get_tasks");
    const text = JSON.stringify(result);
    expect(result.structuredContent.error.code).toBe("github_error");
    expect(text).not.toContain(key.pkcs8Pem.split("\n")[1]);
    expect(text).not.toContain(github.currentToken);
  });

  it("refuses an over-broad installation token with scope_refused", async () => {
    const github = new FakeGitHub({
      dataset: { issues: [], comments: {} },
      now: () => NOW,
      publicKey: key.publicKey,
      tokenResponse: (base) => ({ ...base, repository_selection: "all" }),
    });
    const worker = createWorker({ fetch: github.fetch, now: () => NOW, resolveAgent: agentFromHeader, api: API });
    const result = await client(worker, env(), "cursor").tool("get_tasks");
    expect(result.structuredContent.error.code).toBe("scope_refused");
  });

  it("reuses the installation token across requests in one isolate", async () => {
    const github = new FakeGitHub({ dataset: { issues: [], comments: {} }, now: () => NOW, publicKey: key.publicKey });
    const worker = createWorker({ fetch: github.fetch, now: () => NOW, resolveAgent: agentFromHeader, api: API });
    for (let index = 0; index < 3; index += 1) {
      await client(worker, env(), "cursor").tool("get_tasks");
    }
    expect(github.mints).toBe(1);
  });

  it("gives each request its own subrequest budget", async () => {
    const issues = Array.from({ length: 40 }, (_, index) => ({ number: index + 1, title: "[Task]: x", state: "open", labels: [], body: "", html_url: "" }));
    const github = new FakeGitHub({ dataset: { issues, comments: {} }, now: () => NOW, publicKey: key.publicKey });
    const worker = createWorker({ fetch: github.fetch, now: () => NOW, resolveAgent: agentFromHeader, api: API });
    for (let index = 0; index < 3; index += 1) {
      const before = github.calls.length;
      const result = await client(worker, env(), "cursor").tool("get_tasks", { limit: 40 });
      expect(result.structuredContent.ok).toBe(true);
      expect(result.structuredContent.result.truncated).toBeUndefined();
      expect(github.calls.length - before).toBeLessThanOrEqual(45);
    }
  });

  it("does not serve a non-MCP path", async () => {
    const res = await createWorker().fetch(new Request("https://example.invalid/anything") as any, {}, CTX);
    expect(res.status).toBe(404);
  });
});
