import { verifyJwt } from "./keys";

type Json = Record<string, any>;

export const REPO = "AStrasler/Egeregore";
export const API = "https://api.github.test";

export interface Dataset {
  issues: Json[];
  comments: Record<string, Json[]>;
  pulls?: Record<string, Json>;
  reactions?: Record<string, Json[]>;
}

/** One GitHub call the Worker made, in the same vocabulary record.py logs for the Python store. */
export type Op = Record<string, unknown> & { op: string };

export interface Call {
  method: string;
  url: string;
  path: string;
  auth: string;
  body: Json | null;
  init: RequestInit;
}

const json = (status: number, payload: unknown, headers: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });

/** Secret-shaped placeholders, expanded the same way record.py does. */
const SUBSTITUTIONS: Record<string, string> = {
  "{{ghp}}": "ghp_" + "a".repeat(36),
  "{{sk}}": "sk-" + "b".repeat(30),
  "{{pem}}": "-----BEGIN " + "RSA PRIVATE KEY-----",
};

export function expand<T>(value: T): T {
  if (typeof value === "string") {
    let text: string = value;
    for (const [key, secret] of Object.entries(SUBSTITUTIONS)) text = text.replaceAll(key, secret);
    return text as T;
  }
  if (Array.isArray(value)) return value.map((item) => expand(item)) as T;
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, expand(v)])) as T;
  }
  return value;
}

export interface FakeOptions {
  dataset?: Dataset;
  installationId?: number;
  now?: () => number;
  /** Overrides the access-token response body. */
  tokenResponse?: (base: Json) => Json;
  /** Page size ceiling, so pagination can be exercised with small data. */
  pageSize?: number;
  /** Origin used in Link headers. */
  linkOrigin?: string;
  publicKey?: CryptoKey;
  /**
   * Fail the next N requests with this status. `method` narrows it to one verb,
   * `skip` lets that many matching requests through first, and `text` sends a plain-text body.
   */
  failWith?: { status: number; times: number; match?: RegExp; method?: string; skip?: number; text?: string };
  /** Login on comments the fake creates. */
  botLogin?: string;
}

/** A fake GitHub: the App endpoints plus the read endpoints the Worker uses. */
export class FakeGitHub {
  readonly calls: Call[] = [];
  readonly tokens: string[] = [];
  mints = 0;
  readonly dataset: Dataset;
  readonly options: FakeOptions;
  private seq = 0;
  private nextComment = 900001;

  constructor(options: FakeOptions = {}) {
    this.options = options;
    this.dataset = options.dataset ?? { issues: [], comments: {} };
  }

  /** Requests that changed something. */
  get writes(): Call[] {
    return this.repoCalls.filter((call) => call.method !== "GET");
  }

  /**
   * The repository calls as the Python store logged them. Only the first page of a list is one
   * logical read, so later pages are folded into it.
   */
  get ops(): Op[] {
    const out: Op[] = [];
    for (const call of this.repoCalls) {
      const rest = call.path.slice(`/repos/${REPO}/`.length);
      const page = Number(new URL(call.url).searchParams.get("page") ?? 1);
      let match: RegExpExecArray | null;
      if (call.method === "GET") {
        if (rest === "issues") {
          if (page === 1) out.push({ op: "list_issues", state: new URL(call.url).searchParams.get("state") ?? "open" });
        } else if ((match = /^issues\/(\d+)$/.exec(rest))) out.push({ op: "get_issue", number: Number(match[1]) });
        else if ((match = /^issues\/(\d+)\/comments$/.exec(rest))) {
          if (page === 1) out.push({ op: "list_comments", number: Number(match[1]) });
        } else if ((match = /^issues\/(\d+)\/reactions$/.exec(rest))) {
          if (page === 1) out.push({ op: "list_reactions", number: Number(match[1]) });
        } else if ((match = /^pulls\/(\d+)$/.exec(rest))) out.push({ op: "get_pull", number: Number(match[1]) });
        else out.push({ op: `GET ${rest}` });
      } else if (call.method === "POST" && rest === "issues") {
        out.push({ op: "create_issue", title: call.body?.title, body: call.body?.body, labels: call.body?.labels });
      } else if (call.method === "POST" && (match = /^issues\/(\d+)\/comments$/.exec(rest))) {
        out.push({ op: "create_comment", number: Number(match[1]), body: call.body?.body });
      } else if (call.method === "POST" && (match = /^issues\/(\d+)\/labels$/.exec(rest))) {
        out.push({ op: "add_labels", number: Number(match[1]), labels: call.body?.labels });
      } else if (call.method === "DELETE" && (match = /^issues\/(\d+)\/labels\/(.+)$/.exec(rest))) {
        out.push({ op: "remove_label", number: Number(match[1]), name: decodeURIComponent(match[2]) });
      } else out.push({ op: `${call.method} ${rest}` });
    }
    return out;
  }

  get installation(): number {
    return this.options.installationId ?? 42;
  }

  get currentToken(): string {
    return this.tokens[this.tokens.length - 1] ?? "";
  }

  /** Requests that went to the repository, not to the App endpoints. */
  get repoCalls(): Call[] {
    return this.calls.filter((call) => call.path.startsWith(`/repos/${REPO}/`) && !call.path.endsWith("/installation"));
  }

  fetch = async (url: string, init: RequestInit): Promise<Response> => {
    const parsed = new URL(url);
    const headers = new Headers(init.headers);
    const call: Call = {
      method: (init.method ?? "GET").toUpperCase(),
      url,
      path: parsed.pathname,
      auth: headers.get("authorization") ?? "",
      body: typeof init.body === "string" ? JSON.parse(init.body) : null,
      init,
    };
    this.calls.push(call);
    const fail = this.options.failWith;
    if (
      fail &&
      fail.times > 0 &&
      (!fail.match || fail.match.test(call.path)) &&
      (!fail.method || fail.method === call.method)
    ) {
      if ((fail.skip ?? 0) > 0) {
        fail.skip = (fail.skip ?? 0) - 1;
      } else {
        fail.times -= 1;
        if (fail.text !== undefined) return new Response(fail.text, { status: fail.status });
        return json(fail.status, { message: "injected failure" });
      }
    }
    return this.route(call, parsed);
  };

  private async route(call: Call, parsed: URL): Promise<Response> {
    const now = (this.options.now ?? Date.now)();
    const tokenRoute = /^\/app\/installations\/(\d+)\/access_tokens$/.exec(call.path);
    if (call.method === "POST" && tokenRoute) {
      if (!call.auth.startsWith("Bearer ") || !this.options.publicKey) return json(401, { message: "Bad credentials" });
      try {
        await verifyJwt(call.auth.slice(7), this.options.publicKey);
      } catch {
        return json(401, { message: "Bad credentials" });
      }
      this.mints += 1;
      const token = `ghs_${String(this.mints).padStart(4, "0")}${"t".repeat(32)}`;
      this.tokens.push(token);
      const base: Json = {
        token,
        expires_at: new Date(now + 3600_000).toISOString().replace(/\.\d+Z$/, "Z"),
        permissions: { issues: "write", pull_requests: "read", metadata: "read" },
        repository_selection: "selected",
        repositories: [{ full_name: REPO, name: "Egeregore" }],
      };
      return json(201, this.options.tokenResponse ? this.options.tokenResponse(base) : base);
    }
    if (call.method === "GET" && call.path === `/repos/${REPO}/installation`) {
      return json(200, { id: this.installation });
    }
    if (call.method === "GET" && call.path === "/installation/repositories") {
      return json(200, { total_count: 1, repositories: [{ full_name: REPO, name: "Egeregore" }] });
    }
    if (call.auth !== `Bearer ${this.currentToken}` || !this.currentToken) {
      return json(401, { message: "Bad credentials" });
    }
    const prefix = `/repos/${REPO}/`;
    if (!call.path.startsWith(prefix)) return json(404, { message: "Not Found" });
    const rest = call.path.slice(prefix.length);
    if (call.method !== "GET") return this.write(call, rest, now);
    if (rest === "issues") return this.listIssues(parsed);
    const one = /^issues\/(\d+)$/.exec(rest);
    if (one) {
      const issue = this.dataset.issues.find((item) => item.number === Number(one[1]));
      return issue ? json(200, issue) : json(404, { message: "Not Found" });
    }
    const pull = /^pulls\/(\d+)$/.exec(rest);
    if (pull) {
      const row = this.dataset.pulls?.[pull[1]];
      return row ? json(200, row) : json(404, { message: "Not Found" });
    }
    const reactions = /^issues\/(\d+)\/reactions$/.exec(rest);
    if (reactions) {
      if (!this.dataset.issues.some((item) => item.number === Number(reactions[1]))) return json(404, { message: "Not Found" });
      return this.page(parsed, this.dataset.reactions?.[reactions[1]] ?? []);
    }
    const thread = /^issues\/(\d+)\/comments$/.exec(rest);
    if (thread) {
      const number = Number(thread[1]);
      if (!this.dataset.issues.some((item) => item.number === number)) return json(404, { message: "Not Found" });
      return this.page(parsed, this.dataset.comments[String(number)] ?? []);
    }
    return json(404, { message: "Not Found" });
  }

  private write(call: Call, rest: string, now: number): Response {
    const stamp = new Date(now).toISOString().replace(/\.\d+Z$/, "Z");
    const body = call.body ?? {};
    if (call.method === "POST" && rest === "issues") {
      const number = Math.max(0, ...this.dataset.issues.map((item) => item.number as number)) + 1;
      const created = {
        number,
        title: body.title,
        body: body.body,
        state: "open",
        labels: ((body.labels as string[] | undefined) ?? []).map((name) => ({ name })),
        html_url: `https://github.com/${REPO}/issues/${number}`,
        created_at: stamp,
      };
      this.dataset.issues.push(created);
      this.dataset.comments[String(number)] = [];
      return json(201, created);
    }
    let match: RegExpExecArray | null;
    if (call.method === "POST" && (match = /^issues\/(\d+)\/comments$/.exec(rest))) {
      if (!this.dataset.issues.some((item) => item.number === Number(match![1]))) return json(404, { message: "Not Found" });
      const created = {
        id: this.nextComment++,
        created_at: stamp,
        updated_at: stamp,
        body: body.body,
        user: { login: this.options.botLogin ?? "egregore[bot]" },
      };
      (this.dataset.comments[match[1]] ??= []).push(created);
      return json(201, created);
    }
    if (call.method === "POST" && (match = /^issues\/(\d+)\/labels$/.exec(rest))) {
      const row = this.dataset.issues.find((item) => item.number === Number(match![1]));
      if (!row) return json(404, { message: "Not Found" });
      const have = new Set((row.labels as Json[]).map((entry) => entry.name));
      for (const name of (body.labels as string[]) ?? []) if (!have.has(name)) row.labels.push({ name });
      return json(200, row.labels);
    }
    if (call.method === "DELETE" && (match = /^issues\/(\d+)\/labels\/(.+)$/.exec(rest))) {
      const row = this.dataset.issues.find((item) => item.number === Number(match![1]));
      const name = decodeURIComponent(match[2]);
      if (!row || !(row.labels as Json[]).some((entry) => entry.name === name)) return json(404, { message: "Label does not exist" });
      row.labels = (row.labels as Json[]).filter((entry) => entry.name !== name);
      return json(200, row.labels);
    }
    return json(404, { message: "Not Found" });
  }

  private listIssues(parsed: URL): Response {
    const state = parsed.searchParams.get("state") ?? "open";
    const rows = this.dataset.issues.filter((item) => state === "all" || item.state === state);
    return this.page(parsed, rows);
  }

  private page(parsed: URL, rows: Json[]): Response {
    const per = Math.min(Number(parsed.searchParams.get("per_page") ?? 30), this.options.pageSize ?? 100);
    const page = Number(parsed.searchParams.get("page") ?? 1);
    const slice = rows.slice((page - 1) * per, page * per);
    const headers: Record<string, string> = {};
    if (page * per < rows.length) {
      const next = new URL(parsed.toString());
      next.searchParams.set("page", String(page + 1));
      next.searchParams.set("per_page", String(per));
      const origin = this.options.linkOrigin ?? parsed.origin;
      headers.Link = `<${origin}${next.pathname}${next.search}>; rel="next"`;
    }
    this.seq += 1;
    return json(200, slice, headers);
  }
}
