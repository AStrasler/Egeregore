/**
 * GitHub REST client for the read tools: a port of mcp/egregore_mcp/github_api.py
 * narrowed to what get_* needs, plus the guards the Python server does not have
 * because it is not running under Cloudflare's per-request limits.
 *
 * Workers Free allows 50 subrequests per request. Every outbound fetch here
 * takes one unit from a shared SubrequestBudget and fails closed when the
 * budget is gone, so a large repository can never turn into a platform error.
 */
import { redactText, type Json } from "./coordination";
import { pyHead, pySplitLines, pyStrip } from "./pycompat";

export const GITHUB_API = "https://api.github.com";
export const DEFAULT_REPO = "AStrasler/Egeregore";
/** Under the Free plan's 50. Leaves headroom for the runtime's own subrequests. */
export const DEFAULT_SUBREQUEST_BUDGET = 45;
/** Most pages one list call may read. The Python client stops after 10. */
export const MAX_PAGES = 10;

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

export class GitHubError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "GitHubError";
  }
}

export class SubrequestBudgetError extends GitHubError {
  constructor() {
    super(0, "This request used its allowance of outbound GitHub calls.");
    this.name = "SubrequestBudgetError";
  }
}

/** One per tool call. Never shared across requests. */
export class SubrequestBudget {
  private used = 0;

  constructor(readonly max: number = DEFAULT_SUBREQUEST_BUDGET) {}

  get remaining(): number {
    return this.max - this.used;
  }

  take(): void {
    if (this.used >= this.max) {
      throw new SubrequestBudgetError();
    }
    this.used += 1;
  }
}

/** Replace each known secret in a piece of text. Longest first, as the Python scrubber does. */
export class Scrubber {
  private readonly secrets: string[] = [];

  remember(value: string): void {
    if (value.length < 8 || this.secrets.includes(value)) {
      return;
    }
    this.secrets.push(value);
  }

  rememberKey(value: string): void {
    this.remember(value);
    for (const line of pySplitLines(value)) {
      const piece = pyStrip(line);
      if (piece) {
        this.remember(piece);
      }
    }
  }

  scrub(text: string): string {
    let cleaned = text;
    for (const secret of [...this.secrets].sort((a, b) => b.length - a.length)) {
      cleaned = cleaned.replaceAll(secret, "[redacted]");
    }
    return redactText(cleaned);
  }
}

export interface RawResponse {
  status: number;
  text: string;
  link: string | null;
}

export interface SendOptions {
  fetch: FetchLike;
  budget: SubrequestBudget;
  scrubber: Scrubber;
  method: "GET" | "POST";
  url: string;
  bearer: string;
  body?: unknown;
}

/**
 * One authenticated GitHub call. Returns the response whatever its status.
 * Throws GitHubError(0) when the connection fails and SubrequestBudgetError
 * when the allowance is gone. Redirects are not followed, so a bearer token
 * can never be sent to another host.
 */
export async function send(options: SendOptions): Promise<RawResponse> {
  options.budget.take();
  options.scrubber.remember(options.bearer);
  const headers: Record<string, string> = {
    Authorization: `Bearer ${options.bearer}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "egregore-worker/0.1",
  };
  const init: RequestInit = { method: options.method, headers, redirect: "manual" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await options.fetch(options.url, init);
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new GitHubError(0, `GitHub connection failed: ${options.scrubber.scrub(reason)}`);
  }
  let text: string;
  try {
    text = await response.text();
  } catch {
    throw new GitHubError(0, "GitHub connection failed: the response could not be read");
  }
  return { status: response.status, text, link: response.headers.get("Link") };
}

/** "GitHub HTTP <status>: <first line, redacted, 300 characters>" */
export function safeError(scrubber: Scrubber, status: number, detail: string): string {
  const lines = pySplitLines(pyStrip(scrubber.scrub(detail)));
  const line = pyHead(lines.length > 0 ? lines[0] : "GitHub request failed", 300);
  return `GitHub HTTP ${status}: ${line}`;
}

/** The rel="next" target of a Link header, or null. */
export function nextLink(header: string | null): string | null {
  if (!header) {
    return null;
  }
  for (const part of header.split(",")) {
    const piece = pyStrip(part);
    if (!piece.includes('rel="next"')) {
      continue;
    }
    const start = piece.indexOf("<");
    const end = piece.indexOf(">");
    if (start !== -1 && end !== -1) {
      return piece.slice(start + 1, end);
    }
  }
  return null;
}

/** A next link is followed only when it stays on the API origin. */
export function sameOrigin(api: string, url: string): boolean {
  try {
    return new URL(url).origin === new URL(api).origin;
  } catch {
    return false;
  }
}

export interface Listing {
  items: Json[];
  /** True when GitHub reported more pages than this call was allowed to read. */
  truncated: boolean;
}

export interface GitHubReader {
  readonly budget: SubrequestBudget;
  listIssues(state: string, maxPages: number): Promise<Listing>;
  getIssue(number: number): Promise<Json>;
  listComments(number: number): Promise<Json[]>;
}

export interface GitHubClientOptions {
  api?: string;
  repo: string;
  /** Returns an installation token. Called once per GitHub call. */
  token: () => Promise<string>;
  fetch: FetchLike;
  budget: SubrequestBudget;
  scrubber?: Scrubber;
}

function asObjects(payload: unknown): Json[] {
  return (payload as unknown[]).filter(
    (item): item is Json => typeof item === "object" && item !== null && !Array.isArray(item),
  );
}

export class GitHubClient implements GitHubReader {
  readonly api: string;
  readonly budget: SubrequestBudget;
  private readonly repo: string;
  private readonly tokenProvider: () => Promise<string>;
  private readonly fetchImpl: FetchLike;
  private readonly scrubber: Scrubber;

  constructor(options: GitHubClientOptions) {
    this.api = (options.api ?? GITHUB_API).replace(/\/+$/, "");
    this.repo = options.repo;
    this.tokenProvider = options.token;
    this.fetchImpl = options.fetch;
    this.budget = options.budget;
    this.scrubber = options.scrubber ?? new Scrubber();
  }

  async listIssues(state: string, maxPages: number): Promise<Listing> {
    const params = new URLSearchParams({ state, per_page: "100" });
    return this.list("issues", params, "issues response was not a list", maxPages);
  }

  async getIssue(number: number): Promise<Json> {
    const { payload } = await this.once(this.url(`issues/${number}`));
    if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
      throw new GitHubError(502, "GitHub HTTP 502: issue response was not an object");
    }
    return payload as Json;
  }

  async listComments(number: number): Promise<Json[]> {
    const params = new URLSearchParams({ per_page: "100" });
    const listing = await this.list(
      `issues/${number}/comments`,
      params,
      "comments response was not a list",
      MAX_PAGES,
    );
    if (listing.truncated) {
      // Reading part of a thread would give a wrong lease. Python fails the same way.
      throw new GitHubError(502, "GitHub HTTP 502: stopped after 10 pages");
    }
    return listing.items;
  }

  private url(suffix: string, params?: URLSearchParams): string {
    const base = `${this.api}/repos/${this.repo}/${suffix}`;
    return params ? `${base}?${params.toString()}` : base;
  }

  private async list(
    suffix: string,
    params: URLSearchParams,
    notAList: string,
    maxPages: number,
  ): Promise<Listing> {
    const items: Json[] = [];
    let next: string | null = this.url(suffix, params);
    let pages = 0;
    while (next !== null) {
      if (pages >= maxPages) {
        return { items, truncated: true };
      }
      pages += 1;
      const { payload, link } = await this.once(next);
      if (!Array.isArray(payload)) {
        throw new GitHubError(502, `GitHub HTTP 502: ${notAList}`);
      }
      items.push(...asObjects(payload));
      const candidate = nextLink(link);
      if (candidate !== null && !sameOrigin(this.api, candidate)) {
        throw new GitHubError(502, "GitHub HTTP 502: pagination link left the GitHub API");
      }
      next = candidate;
    }
    return { items, truncated: false };
  }

  private async once(url: string): Promise<{ payload: unknown; link: string | null }> {
    const bearer = await this.tokenProvider();
    if (typeof bearer !== "string" || !bearer) {
      throw new GitHubError(0, "GitHub authentication did not return a token");
    }
    const response = await send({
      fetch: this.fetchImpl,
      budget: this.budget,
      scrubber: this.scrubber,
      method: "GET",
      url,
      bearer,
    });
    if (response.status < 200 || response.status > 299) {
      throw new GitHubError(response.status, safeError(this.scrubber, response.status, response.text));
    }
    if (!response.text) {
      return { payload: null, link: response.link };
    }
    try {
      return { payload: JSON.parse(response.text) as unknown, link: response.link };
    } catch {
      throw new GitHubError(502, "GitHub HTTP 502: response was not JSON");
    }
  }
}
