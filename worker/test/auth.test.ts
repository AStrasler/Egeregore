import { beforeAll, describe, expect, it } from "vitest";
import {
  GitHubAppAuth,
  GitHubAppConfigError,
  GitHubAppScopeError,
  importSigningKey,
  normalizePem,
  pemToPkcs8,
  pkcs1ToPkcs8,
} from "../src/github-app";
import { GitHubError, SubrequestBudget, SubrequestBudgetError } from "../src/github";
import { API, FakeGitHub, REPO } from "./helpers/fake-github";
import { pkcs8ToPkcs1, testKey, verifyJwt, type TestKey } from "./helpers/keys";

const T0 = Date.parse("2026-10-27T18:00:00Z");

let key: TestKey;
beforeAll(async () => {
  key = await testKey();
});

function setup(options: ConstructorParameters<typeof FakeGitHub>[0] = {}, pem?: string, installationId: string | null = "42") {
  const clock = { now: T0 };
  const github = new FakeGitHub({ now: () => clock.now, publicKey: key.publicKey, ...options });
  const auth = new GitHubAppAuth({
    api: API,
    appId: "123",
    privateKey: pem ?? key.pkcs8Pem,
    repo: REPO,
    installationId,
    now: () => clock.now,
    fetch: github.fetch,
  });
  return { clock, github, auth, budget: new SubrequestBudget() };
}

describe("GitHub App token", () => {
  it("signs an RS256 JWT and asks for a one-repository, three-permission token", async () => {
    const { github, auth, budget } = setup();
    const token = await auth.token(budget);
    expect(token).toBe(github.currentToken);
    const mint = github.calls[0];
    expect(mint.method).toBe("POST");
    expect(mint.path).toBe("/app/installations/42/access_tokens");
    expect(mint.body).toEqual({
      repositories: ["Egeregore"],
      permissions: { issues: "write", pull_requests: "read", metadata: "read" },
    });
    const { header, claims } = await verifyJwt(mint.auth.slice("Bearer ".length), key.publicKey);
    expect(header).toEqual({ alg: "RS256", typ: "JWT" });
    expect(claims.iss).toBe("123");
    expect(claims.iat).toBe(T0 / 1000 - 60);
    expect((claims.exp as number) - (claims.iat as number)).toBeLessThanOrEqual(9 * 60);
    expect(budget.remaining).toBe(44);
  });

  it("accepts a PKCS#1 key (BEGIN RSA PRIVATE KEY) as GitHub issues it", async () => {
    expect(key.pkcs1Pem).toContain("BEGIN RSA PRIVATE KEY");
    const { github, auth, budget } = setup({}, key.pkcs1Pem);
    expect(await auth.token(budget)).toBe(github.currentToken);
    expect(github.mints).toBe(1);
  });

  it("accepts a key whose newlines were stored as literal backslash-n", async () => {
    const escaped = key.pkcs8Pem.trim().replaceAll("\n", "\\n");
    const { github, auth, budget } = setup({}, `"${escaped}"`);
    expect(await auth.token(budget)).toBe(github.currentToken);
  });

  it("wraps and unwraps PKCS#1 to the same signing key", async () => {
    const pkcs8 = pemToPkcs8(key.pkcs8Pem);
    const rewrapped = pkcs1ToPkcs8(pkcs8ToPkcs1(pkcs8));
    expect([...rewrapped]).toEqual([...pkcs8]);
    expect(normalizePem(key.pkcs8Pem)).toContain("BEGIN PRIVATE KEY");
    await importSigningKey(key.pkcs1Pem);
  });

  it("caches the token and refreshes it five minutes before it expires", async () => {
    const { clock, github, auth, budget } = setup();
    const first = await auth.token(budget);
    clock.now = T0 + 30 * 60_000;
    expect(await auth.token(budget)).toBe(first);
    expect(github.mints).toBe(1);
    clock.now = T0 + 56 * 60_000;
    const second = await auth.token(budget);
    expect(second).not.toBe(first);
    expect(github.mints).toBe(2);
  });

  it("looks the installation up when no id is configured", async () => {
    const { github, auth, budget } = setup({ installationId: 777 }, undefined, null);
    await auth.token(budget);
    expect(github.calls[0].path).toBe(`/repos/${REPO}/installation`);
    expect(github.calls[1].path).toBe("/app/installations/777/access_tokens");
    expect(budget.remaining).toBe(43);
  });

  it("falls back to the installation repository list when the response omits it", async () => {
    const { github, auth, budget } = setup({
      tokenResponse: ({ repositories: _drop, ...rest }) => rest,
    });
    await auth.token(budget);
    expect(github.calls.map((call) => call.path)).toContain("/installation/repositories");
  });

  it.each([
    ["all repositories", (base: any) => ({ ...base, repository_selection: "all" }), /broader than/],
    [
      "an extra repository",
      (base: any) => ({ ...base, repositories: [...base.repositories, { full_name: "AStrasler/Other", name: "Other" }] }),
      /must be only/,
    ],
    ["no repository", (base: any) => ({ ...base, repositories: [] }), /\(empty\)/],
    [
      "a broader permission",
      (base: any) => ({ ...base, permissions: { ...base.permissions, contents: "write" } }),
      /contents:write/,
    ],
    [
      "a higher level",
      (base: any) => ({ ...base, permissions: { ...base.permissions, pull_requests: "write" } }),
      /pull_requests:write/,
    ],
    [
      "a missing permission",
      (base: any) => ({ ...base, permissions: { issues: "write", metadata: "read" } }),
      /pull_requests:missing/,
    ],
    ["no permissions", ({ permissions: _gone, ...rest }: any) => rest, /permissions were missing/],
  ])("refuses a token with %s", async (_name, change, message) => {
    const { auth, budget } = setup({ tokenResponse: change });
    const failure = await auth.token(budget).catch((error) => error);
    expect(failure).toBeInstanceOf(GitHubAppScopeError);
    expect(failure.message).toMatch(message);
  });

  it("matches the repository name without regard to case", async () => {
    const { auth, budget } = setup({
      tokenResponse: (base) => ({ ...base, repositories: [{ full_name: "astrasler/egeregore", name: "egeregore" }] }),
    });
    await expect(auth.token(budget)).resolves.toMatch(/^ghs_/);
  });

  it("never covers a scope failure with an older cached token", async () => {
    let broad = false;
    const { clock, auth, budget } = setup({
      tokenResponse: (base) => (broad ? { ...base, repository_selection: "all" } : base),
    });
    await auth.token(budget);
    broad = true;
    clock.now = T0 + 58 * 60_000;
    await expect(auth.token(budget)).rejects.toBeInstanceOf(GitHubAppScopeError);
  });

  it("keeps serving a still-valid cached token when GitHub briefly fails", async () => {
    const clock = { now: T0 };
    const github = new FakeGitHub({ now: () => clock.now, publicKey: key.publicKey });
    let outage = false;
    const auth = new GitHubAppAuth({
      api: API,
      appId: "123",
      privateKey: key.pkcs8Pem,
      repo: REPO,
      installationId: "42",
      now: () => clock.now,
      fetch: async (url, init) =>
        outage ? new Response(JSON.stringify({ message: "unavailable" }), { status: 503 }) : github.fetch(url, init),
    });
    const budget = new SubrequestBudget();
    const first = await auth.token(budget);
    outage = true;
    clock.now = T0 + 56 * 60_000; // inside the refresh window, token still valid
    expect(await auth.token(budget)).toBe(first);
    clock.now = T0 + 61 * 60_000; // really expired: the outage now surfaces
    await expect(auth.token(budget)).rejects.toBeInstanceOf(GitHubError);
  });

  it("explains an unusable key without echoing it", async () => {
    const body = key.pkcs8Pem.split("\n")[1];
    const broken = `-----BEGIN PRIVATE KEY-----\n${body}\n-----END PRIVATE KEY-----\n`;
    const { auth, budget } = setup({}, broken);
    const failure = await auth.token(budget).catch((error) => error);
    expect(failure).toBeInstanceOf(GitHubAppConfigError);
    expect(failure.message).not.toContain(body);
  });

  it.each([
    ["an encrypted key", "-----BEGIN ENCRYPTED PRIVATE KEY-----\nAAAA\n-----END ENCRYPTED PRIVATE KEY-----\n"],
    ["an EC key", "-----BEGIN EC PRIVATE KEY-----\nAAAA\n-----END EC PRIVATE KEY-----\n"],
    ["nonsense", "not a key"],
  ])("rejects %s as a configuration error", async (_name, pem) => {
    const { auth, budget } = setup({}, pem);
    await expect(auth.token(budget)).rejects.toBeInstanceOf(GitHubAppConfigError);
  });

  it("rejects bad configuration before any request", () => {
    const base = { api: API, privateKey: "x", fetch: async () => new Response("") };
    expect(() => new GitHubAppAuth({ ...base, appId: "abc", repo: REPO })).toThrow(GitHubAppConfigError);
    expect(() => new GitHubAppAuth({ ...base, appId: "1", repo: "nope" })).toThrow(GitHubAppConfigError);
    expect(() => new GitHubAppAuth({ ...base, appId: "1", repo: REPO, installationId: "x" })).toThrow(GitHubAppConfigError);
  });

  it("scrubs a token or JWT that GitHub echoes back in an error", async () => {
    let seen = "";
    const auth = new GitHubAppAuth({
      api: API,
      appId: "123",
      privateKey: key.pkcs8Pem,
      repo: REPO,
      installationId: "42",
      now: () => T0,
      fetch: async (_url, init) => {
        seen = new Headers(init.headers).get("authorization")!.slice(7);
        return new Response(JSON.stringify({ message: `bad jwt ${seen}` }), { status: 401 });
      },
    });
    const failure = await auth.token(new SubrequestBudget()).catch((error) => error);
    expect(failure).toBeInstanceOf(GitHubError);
    expect(seen.length).toBeGreaterThan(50);
    expect(failure.message).not.toContain(seen);
    expect(failure.message).toContain("[redacted]");
  });

  it("stops when the subrequest budget is spent", async () => {
    const { auth } = setup();
    await expect(auth.token(new SubrequestBudget(0))).rejects.toBeInstanceOf(SubrequestBudgetError);
  });

  it("does not follow a redirect with the bearer token", async () => {
    const calls: RequestInit[] = [];
    const auth = new GitHubAppAuth({
      api: API,
      appId: "123",
      privateKey: key.pkcs8Pem,
      repo: REPO,
      installationId: "42",
      now: () => T0,
      fetch: async (_url, init) => {
        calls.push(init);
        return new Response("", { status: 302, headers: { location: "https://evil.example/" } });
      },
    });
    await expect(auth.token(new SubrequestBudget())).rejects.toBeInstanceOf(GitHubError);
    expect(calls).toHaveLength(1);
    expect(calls[0].redirect).toBe("manual");
  });
});
