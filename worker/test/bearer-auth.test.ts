import { beforeAll, describe, expect, it } from "vitest";
import {
  MAX_RECORDS,
  PERMISSIONS,
  TOKEN_AGENTS,
  TOOL_PERMISSIONS,
  authenticateToken,
  bearerToken,
  constantTimeEqual,
  decide,
  loadTokenConfig,
  parseTokenConfig,
  safeRepo,
  safeSegments,
  sha256,
  toHex,
  type Permission,
  type Principal,
} from "../src/auth";
import { createWorker } from "../src/index";
import { API, FakeGitHub, REPO } from "./helpers/fake-github";
import { testKey, type TestKey } from "./helpers/keys";
import { CTX, HEADERS, message } from "./helpers/rpc";
import { bearer, randomToken, recordFor, type RecordOptions } from "./helpers/tokens";
import model from "./parity/auth-model.json";
import vectors from "./parity/repo-names.json";

const NOW = Date.parse("2026-10-27T18:00:00Z");
const ALL_TOOLS = Object.keys(TOOL_PERMISSIONS).sort();
const ARGS: Record<string, Record<string, unknown>> = {
  ping: {},
  get_tasks: {},
  get_task: { issue_number: 1 },
  get_task_history: { issue_number: 1 },
  get_owner_decisions: {},
  get_specialists: {},
  claim_task: { issue_number: 1 },
  renew_lease: { issue_number: 1 },
  release_task: { issue_number: 1, status: "ready", reason: "r" },
  post_progress: { issue_number: 1, done: "d", next: "n" },
  handoff_task: { issue_number: 1, to_agent: "codex", done_so_far: "d", next_action: "n" },
  request_review: { pull_number: 1 },
};
const DATASET = () => ({
  issues: [
    { number: 1, title: "[Task]: one", state: "open", labels: [{ name: "type:task" }, { name: "status:ready" }], body: "### Role\n\ncursor\n", html_url: `https://github.com/${REPO}/issues/1` },
  ],
  comments: { "1": [] as any[] },
});

let key: TestKey;
beforeAll(async () => {
  key = await testKey();
});

function env(tokens: unknown, extra: Record<string, string> = {}): Record<string, string> {
  const secret: Record<string, string> = tokens === undefined ? {} : { EGREGORE_AGENT_TOKENS: typeof tokens === "string" ? tokens : JSON.stringify(tokens) };
  return { EGREGORE_APP_ID: "123", EGREGORE_APP_INSTALLATION_ID: "42", EGREGORE_APP_PRIVATE_KEY: key.pkcs8Pem, ...secret, ...extra };
}

/** Call one tool through the real worker with the real bearer path. */
async function call(
  worker: ReturnType<typeof createWorker>,
  settings: Record<string, string>,
  header: Record<string, string>,
  name: string,
  args: Record<string, unknown> = ARGS[name] ?? {},
): Promise<any> {
  const request = new Request("https://example.invalid/mcp", {
    method: "POST",
    headers: { ...HEADERS, ...header },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }),
  });
  const res = await worker.fetch(request as any, settings, CTX);
  return (await message(res)).result;
}

function bareWorker(github = new FakeGitHub({ dataset: DATASET(), now: () => NOW, publicKey: key.publicKey })) {
  return { github, worker: createWorker({ fetch: github.fetch, now: () => NOW, api: API }) };
}

const UNAUTHENTICATED = "unauthenticated";

describe("safe repository and path checks", () => {
  it("accepts every valid row and refuses every invalid row of the shared vectors", () => {
    for (const row of vectors.valid) expect(safeRepo(row), JSON.stringify(row)).toBe(row);
    for (const row of vectors.invalid) expect(safeRepo(row), JSON.stringify(row)).toBeNull();
  });

  it("rejects empty, `.` and `..` segments in any path", () => {
    expect(safeSegments("a/b/c")).toEqual(["a", "b", "c"]);
    for (const bad of ["a//b", "/a", "a/", "a/./b", "a/../b", "..", ".", "a/b/..", "a\\b", "", 5, null, undefined]) {
      expect(safeSegments(bad), String(bad)).toBeNull();
    }
  });
});

describe("the permission model shared with the Python side", () => {
  it("matches worker/test/parity/auth-model.json exactly", () => {
    expect([...PERMISSIONS]).toEqual(model.permissions);
    expect([...TOKEN_AGENTS]).toEqual(model.token_agents);
    expect(TOOL_PERMISSIONS).toEqual(model.tool_permissions);
  });
});

describe("hashing and comparison", () => {
  it("hashes with SHA-256", async () => {
    expect(toHex(await sha256("abc"))).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });

  it("compares in constant time and refuses unequal lengths", () => {
    const a = new Uint8Array([1, 2, 3]);
    expect(constantTimeEqual(a, new Uint8Array([1, 2, 3]))).toBe(true);
    expect(constantTimeEqual(a, new Uint8Array([1, 2, 4]))).toBe(false);
    expect(constantTimeEqual(a, new Uint8Array([9, 2, 3]))).toBe(false);
    expect(constantTimeEqual(a, new Uint8Array([1, 2]))).toBe(false);
  });

  it("reads one well-formed bearer header and nothing else", () => {
    const token = randomToken();
    expect(bearerToken(`Bearer ${token}`)).toBe(token);
    expect(bearerToken(`bearer ${token}`)).toBe(token);
    for (const bad of [null, "", token, `Basic ${token}`, `Bearer  ${token}`, `Bearer ${token} extra`, `Bearer ${token}\n`, "Bearer short", `Bearer ${"a".repeat(257)}`, "Bearer " + "a".repeat(31), `Bearer a"b${"c".repeat(40)}`]) {
      expect(bearerToken(bad), String(bad)).toBeNull();
    }
  });
});

describe("token config", () => {
  const record = async (options: RecordOptions = {}) => recordFor(randomToken(), options);

  it("accepts a well-formed config and several tokens for one agent", async () => {
    const config = parseTokenConfig(JSON.stringify([await record(), await record({ agent: "codex", permissions: ["read"], tools: ["get_tasks"] }), await record({ label: "second cursor key" })]));
    expect(config.ok).toBe(true);
  });

  it("rejects the whole config for any single bad record, and never echoes a value", async () => {
    const good = await record();
    const secret = "SENTINEL-value-that-must-not-appear";
    const bad: Record<string, unknown>[] = [
      { ...good, sha256: "abc" },
      { ...good, sha256: secret },
      { ...good, sha256: "g".repeat(64) },
      { ...good, agent: secret },
      { ...good, agent: "aaron" },
      { ...good, agent: undefined },
      { ...good, permissions: [] },
      { ...good, permissions: ["read", secret] },
      { ...good, permissions: ["admin"] },
      { ...good, permissions: "read" },
      { ...good, permissions: undefined },
      { ...good, repos: [] },
      { ...good, repos: undefined },
      { ...good, repos: ["*"] },
      { ...good, repos: ["owner/*"] },
      { ...good, repos: ["../.."] },
      { ...good, repos: ["owner//name"] },
      { ...good, repos: [secret] },
      { ...good, repos: Array.from({ length: 17 }, (_, index) => `owner/repo${index}`) },
      { ...good, tools: [] },
      { ...good, tools: undefined },
      { ...good, tools: [secret] },
      { ...good, tools: ["*", "get_tasks"] },
      { ...good, tools: "*" },
      { ...good, label: 5 },
      { ...good, label: "x".repeat(81) },
      { ...good, [secret]: true },
      secret as unknown as Record<string, unknown>,
      null as unknown as Record<string, unknown>,
    ];
    for (const entry of bad) {
      const config = parseTokenConfig(JSON.stringify([good, entry]));
      expect(config.ok, JSON.stringify(entry)).toBe(false);
      if (!config.ok) expect(JSON.stringify(config.problems)).not.toContain(secret);
    }
  });

  it("rejects non-JSON, non-array, empty, oversized, unset and duplicate configs", async () => {
    const good = await record();
    for (const raw of [undefined, null, 5, "", "   ", "{", "{}", "[]", '"x"', "null", JSON.stringify([good, good]), JSON.stringify(Array.from({ length: MAX_RECORDS + 1 }, () => good)), JSON.stringify([{ ...good, label: "x".repeat(20000) }])]) {
      expect(parseTokenConfig(raw).ok, String(raw).slice(0, 30)).toBe(false);
    }
  });

  it("treats the same hash in different letter case as a duplicate", async () => {
    const good = await record();
    const upper = { ...good, sha256: String(good.sha256).toUpperCase() };
    expect(parseTokenConfig(JSON.stringify([good, upper])).ok).toBe(false);
    expect(parseTokenConfig(JSON.stringify([upper])).ok).toBe(true);
  });

  it("parses once per distinct secret and re-parses when the secret changes", async () => {
    const one = JSON.stringify([await record()]);
    expect(loadTokenConfig(one)).toBe(loadTokenConfig(one));
    const two = JSON.stringify([await record({ agent: "codex" })]);
    expect(loadTokenConfig(two)).not.toBe(loadTokenConfig(one));
  });
});

describe("token lookup", () => {
  it("finds the right principal among several tokens and refuses a near miss", async () => {
    const [a, b] = [randomToken(), randomToken()];
    const config = parseTokenConfig(JSON.stringify([await recordFor(a, { agent: "cursor" }), await recordFor(b, { agent: "codex" })]));
    expect((await authenticateToken(a, config))?.agent).toBe("cursor");
    expect((await authenticateToken(b, config))?.agent).toBe("codex");
    const flipped = a.slice(0, -1) + (a.endsWith("A") ? "B" : "A");
    for (const wrong of [flipped, a.toUpperCase(), a + "x", a.slice(1), randomToken(), "", null]) {
      expect(await authenticateToken(wrong, config), String(wrong)).toBeNull();
    }
  });

  it("authenticates nobody against a rejected config", async () => {
    const token = randomToken();
    const config = parseTokenConfig(JSON.stringify([await recordFor(token), { nonsense: true }]));
    expect(config.ok).toBe(false);
    expect(await authenticateToken(token, config)).toBeNull();
  });

  it("does not accept the stored hash as a token", async () => {
    const token = randomToken();
    const stored = String((await recordFor(token)).sha256);
    const config = parseTokenConfig(JSON.stringify([await recordFor(token)]));
    expect(await authenticateToken(stored, config)).toBeNull();
  });
});

describe("decisions", () => {
  const principal = (permissions: Permission[], tools: string[] | null = null, repos = [REPO.toLowerCase()]): Principal => ({
    agent: "cursor",
    permissions: new Set(permissions),
    repos: new Set(repos),
    tools: tools === null ? null : new Set(tools),
  });

  it("maps each tool to exactly one permission, and request_review is its own", () => {
    expect(new Set(Object.values(TOOL_PERMISSIONS))).toEqual(new Set(PERMISSIONS));
    expect(TOOL_PERMISSIONS.request_review).toBe("review");
    expect(TOOL_PERMISSIONS.claim_task).toBe("write");
    expect(TOOL_PERMISSIONS.get_tasks).toBe("read");
  });

  it("allows only the permission a tool needs, with no implication between them", () => {
    for (const held of PERMISSIONS) {
      for (const tool of ALL_TOOLS) {
        const decision = decide(principal([held]), tool, REPO);
        expect(decision.allowed, `${held} ${tool}`).toBe(TOOL_PERMISSIONS[tool] === held);
        if (!decision.allowed) expect(decision.code).toBe("forbidden");
      }
    }
    expect(decide(principal([]), "get_tasks", REPO).allowed).toBe(false);
  });

  it("refuses an unknown tool and any tool outside the allow-list", () => {
    expect(decide(principal([...PERMISSIONS]), "merge_pull", REPO)).toMatchObject({ allowed: false, code: "forbidden" });
    expect(decide(principal([...PERMISSIONS]), "toString", REPO)).toMatchObject({ allowed: false, code: "forbidden" });
    expect(decide(principal([...PERMISSIONS], ["get_tasks"]), "get_task", REPO).allowed).toBe(false);
    expect(decide(principal([...PERMISSIONS], ["get_tasks"]), "get_tasks", REPO).allowed).toBe(true);
    expect(decide(principal([...PERMISSIONS], []), "get_tasks", REPO).allowed).toBe(false);
  });

  it("refuses a repository outside the allow-list, an empty list, or an unsafe name", () => {
    expect(decide(principal([...PERMISSIONS]), "get_tasks", "AStrasler/Egeregore").allowed).toBe(true);
    expect(decide(principal([...PERMISSIONS]), "get_tasks", "astrasler/EGEREGORE").allowed).toBe(true);
    for (const repo of ["AStrasler/Other", "Other/Egeregore", "../..", "AStrasler/..", "", "AStrasler/Egeregore/x"]) {
      expect(decide(principal([...PERMISSIONS]), "get_tasks", repo).allowed, repo).toBe(false);
    }
    expect(decide(principal([...PERMISSIONS], null, []), "get_tasks", REPO).allowed).toBe(false);
  });

  it("returns unauthenticated, not forbidden, when there is no principal", () => {
    for (const tool of [...ALL_TOOLS, "merge_pull"]) {
      expect(decide(null, tool, REPO)).toMatchObject({ allowed: false, code: UNAUTHENTICATED });
    }
  });
});

describe("the Worker with bearer tokens", () => {
  it("covers exactly the tools the server registers", async () => {
    const { worker } = bareWorker();
    const request = new Request("https://example.invalid/mcp", {
      method: "POST",
      headers: HEADERS,
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }),
    });
    const listed = (await message(await worker.fetch(request as any, {}, CTX))).result.tools.map((tool: any) => tool.name).sort();
    expect(listed).toEqual(ALL_TOOLS);
  });

  it("answers `unauthenticated` to every tool with no config, an invalid config, no header or a wrong token, and makes no GitHub call", async () => {
    const token = randomToken();
    const good = await recordFor(token);
    const cases: [string, Record<string, string>, Record<string, string>][] = [
      ["no config, no header", env(undefined), {}],
      ["no config, a token", env(undefined), bearer(token)],
      ["empty config", env(""), bearer(token)],
      ["malformed config", env("{not json"), bearer(token)],
      ["a config with one bad record", env([good, { ...good, permissions: ["admin"] }]), bearer(token)],
      ["no header", env([good]), {}],
      ["wrong token", env([good]), bearer(randomToken())],
      ["the stored hash as a token", env([good]), bearer(String(good.sha256))],
      ["wrong scheme", env([good]), { authorization: `Basic ${token}` }],
      ["token in another header", env([good]), { "x-api-key": token }],
    ];
    const seen = new Set<string>();
    for (const [label, settings, header] of cases) {
      const { github, worker } = bareWorker();
      for (const name of ALL_TOOLS) {
        const result = await call(worker, settings, header, name);
        expect(result.structuredContent.error.code, `${label}: ${name}`).toBe(UNAUTHENTICATED);
        const text = JSON.stringify(result);
        seen.add(text.replace(/"id":\d+/g, ""));
        expect(text).not.toContain(token);
        expect(text).not.toContain(String(good.sha256));
      }
      expect(github.calls, label).toHaveLength(0);
    }
    // Every cause gives the same answer, so a caller learns nothing about which part failed.
    expect(seen.size).toBe(1);
  });

  it("lets a valid token through and takes the agent from the token alone", async () => {
    const [cursorToken, codexToken] = [randomToken(), randomToken()];
    const tokens = [await recordFor(cursorToken, { agent: "cursor" }), await recordFor(codexToken, { agent: "codex" })];
    const github = new FakeGitHub({ dataset: DATASET(), now: () => NOW, publicKey: key.publicKey });
    const worker = createWorker({ fetch: github.fetch, now: () => NOW, api: API });
    // The caller tries to name another agent. The extra argument is ignored.
    const reply = await call(worker, env(tokens), bearer(cursorToken), "claim_task", { issue_number: 1, agent: "codex", agent_name: "codex" });
    expect(reply.structuredContent.ok).toBe(true);
    expect(reply.structuredContent.result.identity.agent).toBe("cursor");
    const posted = github.writes.filter((write) => /comments$/.test(write.path));
    expect(posted).toHaveLength(1);
    expect(String(posted[0].body?.body)).toContain("Claim-Agent: cursor");
    expect(String(posted[0].body?.body)).not.toContain("Claim-Agent: codex");
    // The codex token is a different identity: the live lease is cursor's, so it conflicts.
    const second = await call(worker, env(tokens), bearer(codexToken), "claim_task", { issue_number: 1 });
    expect(second.structuredContent.result?.identity?.agent ?? second.structuredContent.error?.details?.identity?.agent).not.toBe("cursor");
  });

  it("answers `forbidden` for a valid token with the wrong permission, and makes no GitHub call", async () => {
    for (const held of PERMISSIONS) {
      const token = randomToken();
      const { github, worker } = bareWorker();
      const settings = env([await recordFor(token, { permissions: [held] })]);
      for (const name of ALL_TOOLS) {
        const result = await call(worker, settings, bearer(token), name);
        if (TOOL_PERMISSIONS[name] === held) {
          expect(result.structuredContent?.error?.code, `${held} ${name}`).not.toBe("forbidden");
        } else {
          expect(result.structuredContent.error.code, `${held} ${name}`).toBe("forbidden");
          expect(result.structuredContent.error.details.required).toBe(TOOL_PERMISSIONS[name]);
        }
      }
      // A read-only token never reached a write endpoint.
      if (held === "read") expect(github.writes.filter((write) => /comments$/.test(write.path))).toHaveLength(0);
    }
  });

  it("refuses a write tool for a read-only token before any GitHub call", async () => {
    const token = randomToken();
    const { github, worker } = bareWorker();
    const settings = env([await recordFor(token, { permissions: ["read"] })]);
    for (const name of ALL_TOOLS.filter((tool) => TOOL_PERMISSIONS[tool] !== "read")) {
      const result = await call(worker, settings, bearer(token), name);
      expect(result.structuredContent.error.code, name).toBe("forbidden");
    }
    expect(github.calls).toHaveLength(0);
  });

  it("applies the tool allow-list and the repository allow-list", async () => {
    const token = randomToken();
    const { github, worker } = bareWorker();
    const narrow = env([await recordFor(token, { tools: ["get_tasks"] })]);
    expect((await call(worker, narrow, bearer(token), "get_tasks")).structuredContent.ok).toBe(true);
    const refused = await call(worker, narrow, bearer(token), "get_task");
    expect(refused.structuredContent.error).toMatchObject({ code: "forbidden", details: { reason: "tool_not_allowed" } });

    const elsewhere = env([await recordFor(token, { repos: ["AStrasler/Other"] })]);
    const before = github.calls.length;
    for (const name of ALL_TOOLS) {
      const result = await call(worker, elsewhere, bearer(token), name);
      expect(result.structuredContent.error, name).toMatchObject({ code: "forbidden", details: { reason: "repo_not_allowed" } });
    }
    expect(github.calls.length).toBe(before);

    // Re-pointing the server at another repository is also refused for this token.
    const moved = env([await recordFor(token)], { EGREGORE_REPO: "AStrasler/Other" });
    expect((await call(worker, moved, bearer(token), "get_tasks")).structuredContent.error.details.reason).toBe("repo_not_allowed");
  });

  it("refuses a traversal in EGREGORE_REPO even for a token that lists it", async () => {
    const token = randomToken();
    const { github, worker } = bareWorker();
    for (const repo of ["../..", "AStrasler/..", "AStrasler//Egeregore"]) {
      const result = await call(worker, env([await recordFor(token)], { EGREGORE_REPO: repo }), bearer(token), "get_tasks");
      expect(result.structuredContent.error, repo).toMatchObject({ code: "forbidden", details: { reason: "repo_invalid" } });
    }
    expect(github.calls).toHaveLength(0);
  });

  it("never echoes the token, its hash or the config in any response", async () => {
    const token = randomToken();
    const good = await recordFor(token, { permissions: ["read"] });
    const { worker } = bareWorker();
    const settings = env([good]);
    for (const name of ALL_TOOLS) {
      const text = JSON.stringify(await call(worker, settings, bearer(token), name));
      expect(text, name).not.toContain(token);
      expect(text, name).not.toContain(String(good.sha256));
      expect(text, name).not.toContain("EGREGORE_AGENT_TOKENS");
    }
  });
});
