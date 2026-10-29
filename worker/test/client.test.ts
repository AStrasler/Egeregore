import { describe, expect, it } from "vitest";
import {
  GitHubClient,
  GitHubError,
  Scrubber,
  SubrequestBudget,
  SubrequestBudgetError,
  nextLink,
  safeError,
  sameOrigin,
} from "../src/github";
import { API, FakeGitHub, REPO, type Dataset } from "./helpers/fake-github";

const issue = (number: number, state = "open") => ({ number, title: `#${number}`, state, labels: [], body: "" });

function build(dataset: Dataset, options: ConstructorParameters<typeof FakeGitHub>[0] = {}, budget = new SubrequestBudget()) {
  const github = new FakeGitHub({ dataset, ...options });
  github.tokens.push("tok-123456789");
  const client = new GitHubClient({ api: API, repo: REPO, budget, fetch: github.fetch, token: async () => "tok-123456789" });
  return { github, client, budget };
}

describe("GitHub client", () => {
  it("reads every page of a list and sends the headers GitHub expects", async () => {
    const issues = Array.from({ length: 7 }, (_, index) => issue(index + 1));
    const { github, client, budget } = build({ issues, comments: {} }, { pageSize: 3 });
    const listing = await client.listIssues("open", 5);
    expect(listing.items.map((item) => item.number)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(listing.truncated).toBe(false);
    expect(github.calls).toHaveLength(3);
    expect(budget.remaining).toBe(42);
    const init = github.calls[0].init;
    const headers = new Headers(init.headers);
    expect(init.redirect).toBe("manual");
    expect(headers.get("authorization")).toBe("Bearer tok-123456789");
    expect(headers.get("accept")).toBe("application/vnd.github+json");
    expect(headers.get("x-github-api-version")).toBe("2022-11-28");
    expect(headers.get("user-agent")).toBe("egregore-worker/0.1");
    expect(github.calls[0].url).toContain("per_page=100");
  });

  it("reports truncation instead of silently dropping pages", async () => {
    const issues = Array.from({ length: 9 }, (_, index) => issue(index + 1));
    const { client } = build({ issues, comments: {} }, { pageSize: 3 });
    const listing = await client.listIssues("open", 2);
    expect(listing.items).toHaveLength(6);
    expect(listing.truncated).toBe(true);
  });

  it("filters the list by state", async () => {
    const { client } = build({ issues: [issue(1), issue(2, "closed")], comments: {} });
    expect((await client.listIssues("closed", 1)).items.map((item) => item.number)).toEqual([2]);
    expect((await client.listIssues("all", 1)).items).toHaveLength(2);
  });

  it("refuses a pagination link that leaves the GitHub API", async () => {
    const issues = Array.from({ length: 4 }, (_, index) => issue(index + 1));
    const { github, client } = build({ issues, comments: {} }, { pageSize: 2, linkOrigin: "https://evil.example" });
    await expect(client.listIssues("open", 5)).rejects.toThrow(/left the GitHub API/);
    expect(github.calls).toHaveLength(1);
  });

  it("fails a comment thread it cannot read in full", async () => {
    const comments = Array.from({ length: 11 }, (_, index) => ({ id: index + 1, body: "x" }));
    const { client } = build({ issues: [issue(1)], comments: { "1": comments } }, { pageSize: 1 });
    await expect(client.listComments(1)).rejects.toThrow(/stopped after 10 pages/);
  });

  it("reads a comment thread across pages", async () => {
    const comments = Array.from({ length: 250 }, (_, index) => ({ id: index + 1, body: "x" }));
    const { github, client } = build({ issues: [issue(1)], comments: { "1": comments } });
    expect(await client.listComments(1)).toHaveLength(250);
    expect(github.calls).toHaveLength(3);
  });

  it("maps a missing issue to a 404", async () => {
    const { client } = build({ issues: [], comments: {} });
    const failure = await client.getIssue(5).catch((error) => error);
    expect(failure).toBeInstanceOf(GitHubError);
    expect(failure.status).toBe(404);
  });

  it("stops at the subrequest budget before sending another request", async () => {
    const { github, client } = build({ issues: [issue(1)], comments: {} }, {}, new SubrequestBudget(2));
    await client.getIssue(1);
    await client.getIssue(1);
    await expect(client.getIssue(1)).rejects.toBeInstanceOf(SubrequestBudgetError);
    expect(github.calls).toHaveLength(2);
  });

  it("never echoes the token, even when GitHub does", async () => {
    const github = new FakeGitHub();
    const client = new GitHubClient({
      api: API,
      repo: REPO,
      budget: new SubrequestBudget(),
      token: async () => "secret-token-value",
      fetch: async () => new Response(JSON.stringify({ message: "bad secret-token-value here" }), { status: 401 }),
    });
    void github;
    const failure = await client.getIssue(1).catch((error) => error);
    expect(failure.message).not.toContain("secret-token-value");
    expect(failure.message).toContain("[redacted]");
  });

  it("turns a dropped connection into a status-0 error", async () => {
    const client = new GitHubClient({
      api: API,
      repo: REPO,
      budget: new SubrequestBudget(),
      token: async () => "tok-123456789",
      fetch: async () => {
        throw new Error("boom tok-123456789");
      },
    });
    const failure = await client.getIssue(1).catch((error) => error);
    expect(failure.status).toBe(0);
    expect(failure.message).not.toContain("tok-123456789");
  });

  it("rejects a non-JSON body and a non-list list", async () => {
    const make = (body: string) =>
      new GitHubClient({
        api: API,
        repo: REPO,
        budget: new SubrequestBudget(),
        token: async () => "tok-123456789",
        fetch: async () => new Response(body, { status: 200 }),
      });
    await expect(make("<html>").getIssue(1)).rejects.toThrow(/not JSON/);
    await expect(make('{"a":1}').listIssues("open", 1)).rejects.toThrow(/not a list/);
    await expect(make("[1]").getIssue(1)).rejects.toThrow(/not an object/);
  });
});

describe("helpers", () => {
  it("finds the next link", () => {
    expect(nextLink('<https://a/b?page=2>; rel="next", <https://a/b?page=9>; rel="last"')).toBe("https://a/b?page=2");
    expect(nextLink('<https://a/b?page=1>; rel="prev"')).toBeNull();
    expect(nextLink(null)).toBeNull();
  });

  it("compares origins strictly", () => {
    expect(sameOrigin("https://api.github.com", "https://api.github.com/x")).toBe(true);
    expect(sameOrigin("https://api.github.com", "http://api.github.com/x")).toBe(false);
    expect(sameOrigin("https://api.github.com", "https://api.github.com.evil.example/x")).toBe(false);
    expect(sameOrigin("https://api.github.com", "not a url")).toBe(false);
  });

  it("keeps GitHub error text to one redacted line", () => {
    const scrubber = new Scrubber();
    const text = safeError(scrubber, 403, `{"message":"first line\\nsecond ghp_${"a".repeat(36)}"}`);
    expect(text.startsWith("GitHub HTTP 403: ")).toBe(true);
    expect(text).not.toContain("ghp_");
    expect(text.split("\n")).toHaveLength(1);
  });
});
