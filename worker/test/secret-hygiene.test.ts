/**
 * Canary secret-leak tests for the Worker.
 *
 * A canary is a fake secret planted where a real one could travel: the App
 * private key, the installation token, the bearer header, an issue body. A
 * hostile fake GitHub echoes back everything it was sent, in several shapes,
 * and the test collects every place output can escape: tool replies, thrown
 * errors, and console output. If a canary appears in any of them the test
 * fails and names the canary and the channel, never the value.
 *
 * Keys are generated at run time and canaries are assembled from parts, so no
 * secret-shaped string is committed. tests/secret-hygiene.manifest.json lists
 * which source files these tests cover. The Python server has the same suite
 * in tests/test_secret_hygiene.py, which also checks that manifest.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { createWorker } from "../src/index";
import { GitHubAppAuth } from "../src/github-app";
import { GitHubClient, GitHubError, SubrequestBudget } from "../src/github";
import { CoordinationService } from "../src/service";
import { API, FakeGitHub, REPO } from "./helpers/fake-github";
import { testKey, type TestKey } from "./helpers/keys";
import { agentFromHeader, client } from "./helpers/rpc";

const piece = (...parts: string[]): string => parts.join("");

// Not matched by any secret pattern: the dashes keep it from matching. Only a
// scrubber that remembers the exact value keeps this out.
const INSTALL = piece("ghs", "_canary-install-", "token-0123456789-abcdef");
const NOW = Date.parse("2026-10-29T18:00:00Z");

// Shaped like the patterns the service redacts. They arrive in issue text.
const SHAPED: Record<string, string> = {
  ghp: piece("gh", "p_", "a".repeat(36)),
  ghs: piece("gh", "s_", "d".repeat(36)),
  gho: piece("gh", "o_", "e".repeat(36)),
  ghu: piece("gh", "u_", "f".repeat(36)),
  ghr: piece("gh", "r_", "g".repeat(36)),
  pat: piece("github", "_pat_", "B".repeat(30)),
  sk: piece("s", "k-", "c".repeat(30)),
  slack: piece("xo", "xb-", "1234567890-canary"),
  aws: piece("AK", "IA", "CANARY0123456789"),
  pemHeader: piece("-----BEGIN ", "RSA PRIVATE KEY-----"),
};

const STYLES = ["inline", "bare", "json", "padded", "html", "multiline-after"] as const;
type Style = (typeof STYLES)[number];

let key: TestKey;
beforeAll(async () => {
  key = await testKey();
});
afterEach(() => {
  vi.restoreAllMocks();
});

/** A GitHub that records every credential it is sent and echoes it in errors. */
class Hostile {
  style: Style = "inline";
  mintOk = true;
  repoStatus = 500;
  scopeBait = "";
  readonly bearers: string[] = [];
  readonly bodies: string[] = [];

  constructor(private readonly pem: string) {}

  reset(style: Style = "inline", mintOk = true, repoStatus = 500): void {
    this.style = style;
    this.mintOk = mintOk;
    this.repoStatus = repoStatus;
    this.scopeBait = "";
    this.bearers.length = 0;
    this.bodies.length = 0;
  }

  private echo(header: string, body: string, shapedLeak: string): string {
    const bearer = header.replace(/^Bearer /, "");
    switch (this.style) {
      case "inline":
        return `${shapedLeak} Bad credentials for ${header}`;
      case "bare":
        return `${shapedLeak} ${bearer}`;
      case "json":
        return JSON.stringify({ message: `${shapedLeak} Bad credentials`, errors: [{ detail: header, body }] });
      case "padded":
        return `${shapedLeak} ${"x".repeat(200)} ${header}`;
      case "html":
        return `<html><body>${shapedLeak} ${header} ${this.pem}</body></html>`;
      default:
        return `${shapedLeak} Bad credentials\n${header}\n${this.pem}`;
    }
  }

  fetch = async (url: string, init: RequestInit): Promise<Response> => {
    const headers = new Headers(init.headers);
    const header = headers.get("authorization") ?? "";
    const body = typeof init.body === "string" ? init.body : "";
    this.bearers.push(header.replace(/^Bearer /, ""));
    this.bodies.push(body);
    const path = new URL(url).pathname;
    const shaped = Object.values(SHAPED).join(" ");
    if ((init.method ?? "GET") === "POST" && /^\/app\/installations\//.test(path)) {
      if (!this.mintOk) return new Response(this.echo(header, body, shaped), { status: 401 });
      return new Response(
        JSON.stringify({
          token: INSTALL,
          expires_at: new Date(NOW + 3600_000).toISOString().replace(/\.\d+Z$/, "Z"),
          repository_selection: "selected",
          permissions: {
            issues: "write",
            pull_requests: "read",
            metadata: "read",
            ...(this.scopeBait ? { [`bait-${this.scopeBait}`]: "admin" } : {}),
          },
          repositories: [
            { full_name: REPO, name: "Egeregore" },
            ...(this.scopeBait ? [{ full_name: `other/${this.scopeBait}`, name: "x" }] : []),
          ],
        }),
        { status: 201, headers: { "content-type": "application/json" } },
      );
    }
    return new Response(this.echo(header, body, shaped), { status: this.repoStatus });
  };
}

/** Everything that can leave the Worker: tool replies, thrown errors, console output. */
class Channels {
  readonly parts: Record<"reply" | "error" | "console", string[]> = { reply: [], error: [], console: [] };
  private readonly restore: Array<() => void> = [];

  constructor() {
    for (const method of ["log", "info", "warn", "error", "debug"] as const) {
      const spy = vi.spyOn(console, method).mockImplementation((...args: unknown[]) => {
        this.parts.console.push(args.map((arg) => (typeof arg === "string" ? arg : safeJson(arg))).join(" "));
      });
      this.restore.push(() => spy.mockRestore());
    }
  }

  reply(value: unknown): void {
    this.parts.reply.push(safeJson(value));
  }

  error(caught: unknown): void {
    if (caught instanceof Error) {
      this.parts.error.push(caught.message, caught.stack ?? "", safeJson(caught), String(caught));
      if (caught.cause) this.parts.error.push(safeJson(caught.cause), String(caught.cause));
    } else {
      this.parts.error.push(String(caught), safeJson(caught));
    }
  }

  done(): void {
    for (const restore of this.restore) restore();
  }
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, (_k, v) => (v instanceof Error ? { name: v.name, message: v.message, stack: v.stack } : v)) ?? "";
  } catch {
    return String(value);
  }
}

/** Fails with the canary's name and the channel. The value is never printed. */
function expectClean(channels: Channels, canaries: Record<string, string>, where: string): void {
  for (const [channel, chunks] of Object.entries(channels.parts)) {
    const blob = chunks.join("\n");
    for (const [name, value] of Object.entries(canaries)) {
      if (value && blob.includes(value)) expect.fail(`${where}: canary '${name}' reached ${channel}`);
    }
    if (/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/.test(blob)) {
      expect.fail(`${where}: a JWT-shaped string reached ${channel}`);
    }
  }
}

function pemPieces(pem: string): Record<string, string> {
  const found: Record<string, string> = { privateKey: pem.trim() };
  pem
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("-----"))
    .forEach((line, index) => {
      found[`privateKeyLine${index}`] = line;
    });
  return found;
}

function canaries(hostile: Hostile, pem: string): Record<string, string> {
  const found: Record<string, string> = { ...pemPieces(pem), installationToken: INSTALL };
  [...new Set(hostile.bearers)].forEach((bearer, index) => {
    found[`bearerSeenByGitHub${index}`] = bearer;
  });
  return found;
}

function env(extra: Record<string, string> = {}) {
  return { EGREGORE_APP_ID: "123", EGREGORE_APP_INSTALLATION_ID: "42", EGREGORE_APP_PRIVATE_KEY: key.pkcs8Pem, ...extra };
}

const READS: Array<[string, Record<string, unknown>]> = [
  ["get_tasks", {}],
  ["get_task", { issue_number: 1 }],
  ["get_task_history", { issue_number: 1 }],
  ["get_owner_decisions", {}],
];

describe("secret hygiene", () => {
  it("a failed mint does not echo the JWT or the private key", async () => {
    const hostile = new Hostile(key.pkcs8Pem);
    for (const style of STYLES) {
      for (const pem of [key.pkcs8Pem, key.pkcs1Pem]) {
        hostile.reset(style, false);
        const channels = new Channels();
        const auth = new GitHubAppAuth({
          api: API,
          appId: "123",
          privateKey: pem,
          repo: REPO,
          installationId: "42",
          now: () => NOW,
          fetch: hostile.fetch,
        });
        try {
          await auth.token(new SubrequestBudget());
          expect.fail("a failed mint must throw");
        } catch (caught) {
          if (caught instanceof Error && caught.message.includes("must throw")) throw caught;
          channels.error(caught);
        }
        // Through the tool surface as well: the reply is what a client sees.
        const worker = createWorker({ fetch: hostile.fetch, now: () => NOW, api: API, resolveAgent: agentFromHeader });
        for (const [name, args] of READS) channels.reply(await client(worker, env({ EGREGORE_APP_PRIVATE_KEY: pem }), "cursor").tool(name, args));
        channels.done();
        expect(hostile.bearers.length, "the hostile GitHub saw no request").toBeGreaterThan(0);
        expectClean(channels, { ...canaries(hostile, key.pkcs8Pem), ...pemPieces(key.pkcs1Pem), ...SHAPED }, `mint failure, ${style}`);
      }
    }
  });

  it("a failing repository call does not echo the installation token", async () => {
    const hostile = new Hostile(key.pkcs8Pem);
    for (const style of STYLES) {
      for (const status of [401, 403, 422, 500, 502]) {
        hostile.reset(style, true, status);
        const channels = new Channels();
        const worker = createWorker({ fetch: hostile.fetch, now: () => NOW, api: API, resolveAgent: agentFromHeader });
        for (const [name, args] of READS) channels.reply(await client(worker, env(), "cursor").tool(name, args));
        // The client directly, so a thrown error is read too, not only the envelope.
        const auth = new GitHubAppAuth({ api: API, appId: "123", privateKey: key.pkcs8Pem, repo: REPO, installationId: "42", now: () => NOW, fetch: hostile.fetch });
        const budget = new SubrequestBudget();
        const github = new GitHubClient({ api: API, repo: REPO, budget, fetch: hostile.fetch, token: () => auth.token(budget) });
        for (const call of [() => github.getIssue(1), () => github.listComments(1)]) {
          try {
            await call();
            expect.fail("an error status must throw");
          } catch (caught) {
            if (caught instanceof Error && caught.message.includes("must throw")) throw caught;
            expect(caught).toBeInstanceOf(GitHubError);
            channels.error(caught);
          }
        }
        channels.done();
        expect(hostile.bearers).toContain(INSTALL);
        expectClean(channels, { ...canaries(hostile, key.pkcs8Pem), ...SHAPED }, `repository failure ${status}, ${style}`);
      }
    }
  });

  it("unknown secret-shaped text in an error body is redacted", async () => {
    const shaped = Object.fromEntries(Object.entries(SHAPED).map(([name, value]) => [`shaped-${name}`, value]));
    for (const style of STYLES) {
      for (const mode of ["mint", "repository"] as const) {
        const hostile = new Hostile(key.pkcs8Pem);
        hostile.reset(style, mode === "repository");
        const channels = new Channels();
        const worker = createWorker({ fetch: hostile.fetch, now: () => NOW, api: API, resolveAgent: agentFromHeader });
        for (const [name, args] of READS) channels.reply(await client(worker, env(), "cursor").tool(name, args));
        const auth = new GitHubAppAuth({ api: API, appId: "123", privateKey: key.pkcs8Pem, repo: REPO, installationId: "42", now: () => NOW, fetch: hostile.fetch });
        const budget = new SubrequestBudget();
        const github = new GitHubClient({ api: API, repo: REPO, budget, fetch: hostile.fetch, token: () => auth.token(budget) });
        try {
          await github.getIssue(1);
          expect.fail("an error status must throw");
        } catch (caught) {
          if (caught instanceof Error && caught.message.includes("must throw")) throw caught;
          channels.error(caught);
        }
        channels.done();
        expectClean(channels, shaped, `${mode} error body, ${style}`);
        const shown = [...channels.parts.reply, ...channels.parts.error].join("\n");
        expect(shown, `${mode}, ${style}: the echoed text was dropped, not redacted`).toContain("[redacted]");
      }
    }
  });

  it("a scope refusal does not repeat secret-shaped names", async () => {
    const hostile = new Hostile(key.pkcs8Pem);
    hostile.scopeBait = SHAPED.ghs;
    const channels = new Channels();
    const worker = createWorker({ fetch: hostile.fetch, now: () => NOW, api: API, resolveAgent: agentFromHeader });
    for (const [name, args] of READS) channels.reply(await client(worker, env(), "cursor").tool(name, args));
    const auth = new GitHubAppAuth({ api: API, appId: "123", privateKey: key.pkcs8Pem, repo: REPO, installationId: "42", now: () => NOW, fetch: hostile.fetch });
    try {
      await auth.token(new SubrequestBudget());
      expect.fail("a payload with extra scope must be refused");
    } catch (caught) {
      if (caught instanceof Error && caught.message.includes("must be refused")) throw caught;
      channels.error(caught);
    }
    channels.done();
    expectClean(channels, { shapedGhs: SHAPED.ghs }, "scope refusal");
    expect(channels.parts.error.join("\n")).toContain("[redacted]");
  });

  it("a connection failure does not echo the bearer", async () => {
    const hostile = new Hostile(key.pkcs8Pem);
    const refuse = async (url: string, init: RequestInit): Promise<Response> => {
      const header = new Headers(init.headers).get("authorization") ?? "";
      hostile.bearers.push(header.replace(/^Bearer /, ""));
      throw new TypeError(`fetch failed for ${header} ${url}`);
    };
    const channels = new Channels();
    const worker = createWorker({ fetch: refuse, now: () => NOW, api: API, resolveAgent: agentFromHeader });
    for (const [name, args] of READS) channels.reply(await client(worker, env(), "cursor").tool(name, args));
    // Mint succeeds, then the repository call fails to connect with the installation token in the message.
    const mintThenRefuse = async (url: string, init: RequestInit): Promise<Response> => {
      if ((init.method ?? "GET") === "POST") return hostile.fetch(url, init);
      return refuse(url, init);
    };
    const second = createWorker({ fetch: mintThenRefuse, now: () => NOW, api: API, resolveAgent: agentFromHeader });
    for (const [name, args] of READS) channels.reply(await client(second, env(), "cursor").tool(name, args));
    channels.done();
    expect(hostile.bearers).toContain(INSTALL);
    expectClean(channels, canaries(hostile, key.pkcs8Pem), "connection failure");
  });

  it("an unexpected failure returns no internal detail", async () => {
    const leaks = [piece("canary-pat-", "7f3a9c2d41"), INSTALL, key.pkcs8Pem];
    for (const leak of leaks) {
      for (const thrown of [new TypeError(`boom ${leak}`), `boom ${leak}`, { message: leak }]) {
        const channels = new Channels();
        const service = new CoordinationService({
          agent: "cursor",
          github: {
            budget: new SubrequestBudget(),
            listIssues: async () => {
              throw thrown;
            },
            getIssue: async () => {
              throw thrown;
            },
            listComments: async () => {
              throw thrown;
            },
          },
          configProblems: [],
          now: () => NOW,
        });
        channels.reply(await service.getTasks({}));
        channels.reply(await service.getTask(1));
        channels.reply(await service.getTaskHistory(1));
        channels.done();
        expectClean(channels, { leak }, "unexpected failure");
        expect(channels.parts.reply.join("")).toContain("internal_error");
      }
    }
  });

  it("issue text is redacted before it is returned", async () => {
    for (const [name, shaped] of Object.entries(SHAPED)) {
      const channels = new Channels();
      const dataset = {
        issues: [
          {
            number: 1,
            title: "[Task]: canary",
            state: "open",
            html_url: "https://example.test/1",
            labels: [{ name: "type:task" }, { name: "status:ready" }],
            body: `### Role\n\nany\n\n### Outcome\n\ntoken ${shaped}\n\n### Context\n\n${shaped}\n`,
          },
        ],
        comments: {
          "1": [
            {
              id: 11,
              body: `Agent: Cursor\n\nI pasted ${shaped} by mistake`,
              created_at: "2026-10-29T17:00:00Z",
              user: { login: "AStrasler" },
            },
          ],
        },
      };
      const github = new FakeGitHub({ dataset, publicKey: key.publicKey, now: () => NOW });
      const worker = createWorker({ fetch: github.fetch, now: () => NOW, api: API, resolveAgent: agentFromHeader });
      for (const [tool, args] of READS) channels.reply(await client(worker, env(), "cursor").tool(tool, args));
      channels.done();
      expectClean(channels, { [name]: shaped }, `issue text, canary ${name}`);
      expect(channels.parts.reply.join("\n")).toContain("[redacted]");
    }
  });

  it("bad settings answer with variable names only", async () => {
    const bogus = "-----BEGIN PRIVATE KEY-----\ncanaryKeyBody0123456789abcdef\n-----END PRIVATE KEY-----\n";
    const cases: Record<string, Record<string, string>> = {
      unusableKey: env({ EGREGORE_APP_PRIVATE_KEY: bogus }),
      notAPem: env({ EGREGORE_APP_PRIVATE_KEY: "canaryNotAPem0123456789" }),
      badAppId: env({ EGREGORE_APP_ID: "canaryAppId0123456789" }),
      badInstallation: env({ EGREGORE_APP_INSTALLATION_ID: "canaryInstall0123456789" }),
      badRepo: env({ EGREGORE_REPO: "canaryRepo0123456789" }),
    };
    const planted = {
      keyBody: "canaryKeyBody0123456789abcdef",
      notAPem: "canaryNotAPem0123456789",
      appId: "canaryAppId0123456789",
      installation: "canaryInstall0123456789",
      repo: "canaryRepo0123456789",
    };
    for (const [label, values] of Object.entries(cases)) {
      const channels = new Channels();
      const hostile = new Hostile(key.pkcs8Pem);
      const worker = createWorker({ fetch: hostile.fetch, now: () => NOW, api: API, resolveAgent: agentFromHeader });
      for (const [name, args] of READS) channels.reply(await client(worker, values, "cursor").tool(name, args));
      channels.done();
      expectClean(channels, planted, `settings ${label}`);
    }
  });
});
