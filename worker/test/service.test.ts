import { describe, expect, it } from "vitest";
import { GitHubClient, SubrequestBudget } from "../src/github";
import { GitHubAppScopeError } from "../src/github-app";
import { CoordinationService, BATCH_SIZE, LIST_PAGES } from "../src/service";
import { API, FakeGitHub, REPO, type Dataset } from "./helpers/fake-github";

const NOW = Date.parse("2026-10-27T18:00:00Z");

const task = (number: number, extra: Record<string, unknown> = {}) => ({
  number,
  title: `[Task]: item ${number}`,
  state: "open",
  html_url: `https://github.com/${REPO}/issues/${number}`,
  labels: [{ name: "type:task" }],
  body: "### Role\n\ncursor\n",
  ...extra,
});

const comment = (id: number, body: string) => ({
  id,
  created_at: "2026-10-27T12:00:00Z",
  body,
  user: { login: "egregore[bot]" },
});

function service(
  dataset: Dataset,
  options: { agent?: string | null; budget?: SubrequestBudget; pageSize?: number } = {},
) {
  const budget = options.budget ?? new SubrequestBudget();
  const github = new FakeGitHub({ dataset, pageSize: options.pageSize });
  github.tokens.push("tok-123456789");
  const client = new GitHubClient({ api: API, repo: REPO, budget, fetch: github.fetch, token: async () => "tok-123456789" });
  const agent = options.agent === undefined ? "cursor" : options.agent;
  return { github, budget, service: new CoordinationService({ agent, github: client, configProblems: [], now: () => NOW }) };
}

describe("coordination service", () => {
  it("closes every tool when the caller is not identified, without touching GitHub", async () => {
    const { service: closed, github } = service({ issues: [task(1)], comments: {} }, { agent: null });
    const results = [
      await closed.getTasks({}),
      await closed.getTask(1),
      await closed.getTaskHistory(1),
      await closed.getOwnerDecisions({}),
      closed.getSpecialistCatalog({}),
    ];
    for (const result of results) {
      expect(result.ok).toBe(false);
      expect(result.error?.code).toBe("unauthenticated");
    }
    expect(github.calls).toHaveLength(0);
  });

  it("names what is missing when GitHub App settings are absent", async () => {
    const closed = new CoordinationService({
      agent: "cursor",
      github: null,
      configProblems: ["EGREGORE_APP_ID", "EGREGORE_APP_PRIVATE_KEY"],
      now: () => NOW,
    });
    const result = await closed.getTasks({});
    expect(result.error?.code).toBe("unconfigured");
    expect(result.error?.details).toEqual({ missing: ["EGREGORE_APP_ID", "EGREGORE_APP_PRIVATE_KEY"] });
  });

  it("refuses an agent role it does not know", async () => {
    const { service: odd } = service({ issues: [], comments: {} }, { agent: "mallory" });
    expect((await odd.getTasks({})).error?.code).toBe("unconfigured");
  });

  it("validates input before it spends a request", async () => {
    const { service: live, github } = service({ issues: [task(1)], comments: {} });
    expect((await live.getTasks({ limit: 0 })).error?.code).toBe("invalid_input");
    expect((await live.getTasks({ limit: 1.5 })).error?.code).toBe("invalid_input");
    expect((await live.getTasks({ state: "nope" })).error?.code).toBe("invalid_input");
    expect((await live.getTask(0)).error?.code).toBe("invalid_input");
    expect((await live.getTask(Number.NaN)).error?.code).toBe("invalid_input");
    expect((await live.getTaskHistory(-1)).error?.code).toBe("invalid_input");
    expect(github.calls).toHaveLength(0);
  });

  it("maps a missing issue to not_found", async () => {
    const { service: live } = service({ issues: [], comments: {} });
    const result = await live.getTask(9);
    expect(result.error?.code).toBe("not_found");
    expect(result.error?.details).toEqual({ status: 404 });
  });

  it("reads several threads at once, in issue order", async () => {
    const issues = Array.from({ length: 12 }, (_, index) => task(index + 1));
    const { service: live, github } = service({ issues, comments: {} });
    const result = await live.getTasks({ limit: 12 });
    expect((result.result!.tasks as any[]).map((row) => row.number)).toEqual(issues.map((item) => item.number));
    expect(github.calls).toHaveLength(13);
    expect(result.result!.truncated).toBeUndefined();
    expect(BATCH_SIZE).toBeLessThanOrEqual(6);
  });

  it("stops after `limit` rows and fetches no more threads than it needs", async () => {
    const issues = Array.from({ length: 40 }, (_, index) => task(index + 1));
    const { service: live, github } = service({ issues, comments: {} });
    const result = await live.getTasks({ limit: 3 });
    expect(result.result!.count).toBe(3);
    expect(github.calls).toHaveLength(1 + 3);
    expect(result.result!.truncated).toBeUndefined();
  });

  it("returns what it has, marked truncated, when it runs out of subrequests", async () => {
    const issues = Array.from({ length: 30 }, (_, index) => task(index + 1));
    const { service: live, github } = service({ issues, comments: {} }, { budget: new SubrequestBudget(8) });
    const result = await live.getTasks({ limit: 30 });
    expect(result.ok).toBe(true);
    expect(result.result!.count).toBe(7);
    expect(result.result!.truncated).toBe(true);
    expect(String(result.result!.truncated_note)).toMatch(/incomplete/);
    expect(github.calls.length).toBeLessThanOrEqual(8);
  });

  it("never exceeds the 45-request budget on a large repository", async () => {
    const issues = Array.from({ length: 250 }, (_, index) => task(index + 1));
    const comments = Object.fromEntries(issues.map((item) => [String(item.number), [comment(item.number, "hello")]]));
    const { service: live, github, budget } = service({ issues, comments }, { pageSize: 100 });
    const result = await live.getTasks({ limit: 100 });
    expect(result.ok).toBe(true);
    expect(github.calls.length).toBeLessThanOrEqual(45);
    expect(budget.remaining).toBeGreaterThanOrEqual(0);
    // 3 list pages plus 42 comment threads fill the budget. The rest is flagged, not dropped silently.
    expect(result.result!.count).toBe(42);
    expect(result.result!.truncated).toBe(true);
    expect(LIST_PAGES).toBeLessThan(10);
  });

  it("flags a list cut short by the page cap, but not one that already filled its limit", async () => {
    const issues = Array.from({ length: 30 }, (_, index) => task(index + 1));
    const small = service({ issues, comments: {} }, { pageSize: 1 }).service;
    const cut = await small.getTasks({ limit: 100 });
    expect(cut.result!.truncated).toBe(true);
    expect(cut.result!.count).toBe(LIST_PAGES);
    const full = await service({ issues, comments: {} }, { pageSize: 1 }).service.getTasks({ limit: 2 });
    expect(full.result!.truncated).toBeUndefined();
  });

  it("surfaces a GitHub failure as github_error with the status", async () => {
    const github = new FakeGitHub({ dataset: { issues: [task(1)], comments: {} }, failWith: { status: 500, times: 9 } });
    github.tokens.push("tok-123456789");
    const client = new GitHubClient({ api: API, repo: REPO, budget: new SubrequestBudget(), fetch: github.fetch, token: async () => "tok-123456789" });
    const live = new CoordinationService({ agent: "cursor", github: client, configProblems: [], now: () => NOW });
    const result = await live.getTasks({});
    expect(result.error?.code).toBe("github_error");
    expect(result.error?.details).toEqual({ status: 500 });
  });

  it("maps scope refusals and unexpected failures without leaking detail", async () => {
    const scoped = new CoordinationService({
      agent: "cursor",
      github: {
        budget: new SubrequestBudget(),
        listIssues: async () => {
          throw new GitHubAppScopeError("Refusing to start: too broad");
        },
        getIssue: async () => ({}),
        listComments: async () => [],
      },
      configProblems: [],
      now: () => NOW,
    });
    expect((await scoped.getTasks({})).error?.code).toBe("scope_refused");
    const broken = new CoordinationService({
      agent: "cursor",
      github: {
        budget: new SubrequestBudget(),
        listIssues: async () => {
          throw new TypeError("secret internal detail");
        },
        getIssue: async () => ({}),
        listComments: async () => [],
      },
      configProblems: [],
      now: () => NOW,
    });
    const result = await broken.getTasks({});
    expect(result.error?.code).toBe("internal_error");
    expect(JSON.stringify(result)).not.toContain("secret internal detail");
  });

  it("redacts secret-shaped text in bodies and comments", async () => {
    const leaked = "ghp_" + "a".repeat(36);
    const dataset = {
      issues: [task(1, { body: `### Role\n\ncursor\n\nToken: ${leaked}\n` })],
      comments: { "1": [comment(1, `Agent: Cursor\n\nsaw ${leaked}`)] },
    };
    const { service: live } = service(dataset);
    const detail = JSON.stringify(await live.getTask(1));
    const history = JSON.stringify(await live.getTaskHistory(1));
    expect(detail).not.toContain(leaked);
    expect(history).not.toContain(leaked);
    expect(detail).toContain("[redacted]");
  });

  it("serves the specialist catalog to an identified caller", () => {
    const { service: live } = service({ issues: [], comments: {} });
    const all = live.getSpecialistCatalog({});
    expect(all.ok).toBe(true);
    expect(live.getSpecialistCatalog({ kind: "prompt" }).error?.code).toBe("invalid_input");
  });
});
