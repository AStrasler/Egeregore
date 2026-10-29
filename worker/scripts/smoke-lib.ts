// The live smoke test, as a library, so a test can run it against the real
// Worker code with a fake GitHub. smoke-test.mjs is the command line around it.
//
// It checks a deployed Worker from the outside, with the tokens in the private
// file made by provision-agent-tokens.mjs:
//
//   1. Metadata is open: tools/list works with no token.
//   2. Every tool refuses a call with no token, a malformed header, or a wrong
//      token, and the refusal is the same for all three.
//   3. For each token: the tools it may use work, and every tool it may not use
//      answers `forbidden`.
//   4. For each token that may write: claim, post progress, renew, release, on
//      the one test issue, one token at a time. It releases the lease even when
//      a step fails.
//   5. With a test pull request: request_review for each token that may review.
//
// It never prints a token. Every message passes through `scrub` first.
// handoff_task is not called live because it opens a new issue. Tokens that may
// not write are still checked against it, because a refusal posts nothing.

import { TOOL_PERMISSIONS } from "../src/auth.ts";

export interface TokenEntry {
  agent: string;
  label?: string;
  permissions: string[];
  repos?: string[];
  tools: string[];
  token: string;
}

export interface Reply {
  status: number;
  /** The tool's `{ ok, result, error }` envelope, or null when the reply had none. */
  envelope: { ok?: boolean; result?: any; error?: { code?: string; message?: string } } | null;
  /** The first text block of the tool result. `ping` answers with plain text. */
  text: string | null;
  /** A JSON-RPC level error, for example an unknown method. */
  rpcError: string | null;
  /** The raw JSON-RPC result, for tools/list. */
  result: any;
}

/** One HTTP POST of a JSON-RPC body. The caller supplies the transport. */
export type Post = (headers: Record<string, string>, body: unknown) => Promise<Reply>;

export interface SmokeOptions {
  post: Post;
  tokens: TokenEntry[];
  /** An open issue with Role `any`, `type:task` and `status:ready`, made for this test. */
  issue: number;
  /** An open pull request to ask for a review on. Without it the review call is skipped. */
  pull?: number;
  /** False runs the read and refusal checks only. Default true. */
  write?: boolean;
  /** Random text for the wrong-token check. Tests replace it. */
  random?: () => string;
}

export interface Check {
  name: string;
  status: "pass" | "fail" | "skip";
  detail?: string;
}

export interface Report {
  checks: Check[];
  passed: number;
  failed: number;
  skipped: number;
  ok: boolean;
  /** What a person still has to do after the run. */
  cleanup: string[];
}

/** Parse a response body that is JSON, or a one-event server-sent stream, into a Reply. */
export function parseReply(status: number, contentType: string, body: string): Reply {
  const empty: Reply = { status, envelope: null, text: null, rpcError: null, result: null };
  let message: any;
  try {
    if (contentType.includes("text/event-stream")) {
      const data = body.split("\n").find((line) => line.startsWith("data:"));
      message = JSON.parse((data ?? "").slice(5).trim());
    } else {
      message = JSON.parse(body);
    }
  } catch {
    return empty;
  }
  if (message === null || typeof message !== "object") return empty;
  if (message.error) return { ...empty, rpcError: String(message.error.message ?? "error").slice(0, 120) };
  const result = message.result ?? null;
  const text = Array.isArray(result?.content) && typeof result.content[0]?.text === "string" ? result.content[0].text : null;
  let envelope = result?.structuredContent ?? null;
  if (envelope === null && text !== null && text.startsWith("{")) {
    try {
      envelope = JSON.parse(text);
    } catch {
      envelope = null;
    }
  }
  return { status, envelope, text, rpcError: null, result };
}

const SAMPLE_ARGS: Record<string, (issue: number, pull: number) => Record<string, unknown>> = {
  ping: () => ({}),
  get_tasks: () => ({}),
  get_task: (issue) => ({ issue_number: issue }),
  get_task_history: (issue) => ({ issue_number: issue }),
  get_owner_decisions: () => ({}),
  get_specialists: () => ({}),
  claim_task: (issue) => ({ issue_number: issue, lease_hours: 1 }),
  renew_lease: (issue) => ({ issue_number: issue, lease_hours: 1 }),
  release_task: (issue) => ({ issue_number: issue, status: "ready", reason: "Smoke test: a refused call must post nothing." }),
  post_progress: (issue) => ({ issue_number: issue, done: "Smoke test.", next: "Nothing." }),
  handoff_task: (issue) => ({ issue_number: issue, to_agent: "codex", done_so_far: "Smoke test.", next_action: "Nothing." }),
  request_review: (_issue, pull) => ({ pull_number: pull }),
};

const READS = ["get_tasks", "get_task", "get_task_history", "get_owner_decisions", "get_specialists"] as const;
const WRITE_FLOW = ["claim_task", "post_progress", "renew_lease", "release_task"] as const;

export async function runSmoke(options: SmokeOptions): Promise<Report> {
  const { post, tokens, issue } = options;
  const writes = options.write !== false;
  const secrets = tokens.map((entry) => entry.token).filter((token) => token.length > 0);
  const checks: Check[] = [];
  const cleanup: string[] = [];
  let id = 0;

  const scrub = (text: string): string => {
    let out = text;
    for (const secret of secrets) out = out.split(secret).join("[token]");
    return out;
  };
  const record = (name: string, status: Check["status"], detail?: string) => {
    checks.push({ name: scrub(name), status, ...(detail === undefined ? {} : { detail: scrub(detail) }) });
  };
  const expect = (name: string, passed: boolean, detail: string) => record(name, passed ? "pass" : "fail", passed ? undefined : detail);

  const rpc = async (authorization: string | null, method: string, params: unknown = {}): Promise<Reply | null> => {
    id += 1;
    const headers: Record<string, string> = { "content-type": "application/json", accept: "application/json, text/event-stream" };
    if (authorization !== null) headers.authorization = authorization;
    try {
      return await post(headers, { jsonrpc: "2.0", id, method, params });
    } catch (error) {
      record(`${method} request`, "fail", `request failed: ${error instanceof Error ? error.message : String(error)}`);
      return null;
    }
  };
  const call = (authorization: string | null, tool: string, args: Record<string, unknown>) =>
    rpc(authorization, "tools/call", { name: tool, arguments: args });
  const sample = (tool: string) => SAMPLE_ARGS[tool](issue, options.pull ?? 1);
  const code = (reply: Reply | null): string => reply?.envelope?.error?.code ?? (reply === null ? "no reply" : reply.rpcError ?? "none");

  const TOOLS = Object.keys(TOOL_PERMISSIONS).sort();

  // 1. Metadata is open.
  {
    const reply = await rpc(null, "tools/list");
    if (reply !== null) {
      const names: string[] = Array.isArray(reply.result?.tools) ? reply.result.tools.map((tool: any) => String(tool.name)).sort() : [];
      expect("tools/list works with no token and lists every tool", JSON.stringify(names) === JSON.stringify(TOOLS), `got ${names.length} tools, expected ${TOOLS.length}`);
    }
  }

  // 2. Refusal with no valid token.
  {
    const wrong = `Bearer ${(options.random ?? (() => crypto.randomUUID().replaceAll("-", "") + crypto.randomUUID().replaceAll("-", "")))()}`;
    const variants: [string, string | null][] = [
      ["no header", null],
      ["malformed header", "Token not-a-bearer-header"],
      ["wrong token", wrong],
    ];
    const messages = new Set<string>();
    for (const tool of TOOLS) {
      for (const [label, header] of variants) {
        const reply = await call(header, tool, sample(tool));
        const got = code(reply);
        expect(`${tool} with ${label} answers unauthenticated`, got === "unauthenticated", `got ${got}`);
        if (tool === "get_tasks" && reply?.envelope?.error?.message !== undefined) messages.add(reply.envelope.error.message);
      }
    }
    expect("the refusal is identical for no header, a malformed header and a wrong token", messages.size === 1, `${messages.size} different messages`);
  }

  // 3. Each token.
  const writers: TokenEntry[] = [];
  let reviewed = false;
  for (const entry of tokens) {
    const who = entry.label ? `${entry.agent} (${entry.label})` : entry.agent;
    const header = `Bearer ${entry.token}`;
    const mayCall = (tool: string) =>
      entry.permissions.includes(TOOL_PERMISSIONS[tool]) && (entry.tools.includes("*") || entry.tools.includes(tool));

    // 3a. What it may not do is refused, and a refused call changes nothing.
    for (const tool of TOOLS.filter((name) => !mayCall(name))) {
      const reply = await call(header, tool, sample(tool));
      const got = code(reply);
      const leaked = reply?.envelope?.ok === true || (tool === "ping" && reply?.text?.startsWith("pong") === true);
      expect(`${who}: ${tool} is forbidden`, got === "forbidden", leaked ? "the call SUCCEEDED, so this token has more access than its record says" : `got ${got}`);
      if (leaked) {
        cleanup.push(`The ${who} token ran ${tool} when it should have been refused. Check EGREGORE_AGENT_TOKENS, then look at issue #${issue} and pull #${options.pull ?? "-"} for the comment it posted.`);
        if (tool === "claim_task") await call(header, "release_task", { issue_number: issue, status: "ready", reason: "Smoke test: undoing a claim that should have been refused." });
      }
    }

    // 3b. What it may do works.
    if (mayCall("ping")) {
      const reply = await call(header, "ping", {});
      expect(`${who}: ping answers pong`, reply?.text?.startsWith("pong") === true && reply.envelope === null, `got ${code(reply)}`);
    }
    for (const tool of READS.filter(mayCall)) {
      const reply = await call(header, tool, sample(tool));
      expect(`${who}: ${tool} succeeds`, reply?.envelope?.ok === true, `got ${code(reply)}`);
    }

    // 3c. The write flow on the test issue.
    const flowAllowed = WRITE_FLOW.every(mayCall);
    if (!flowAllowed) {
      record(`${who}: write flow on issue #${issue}`, "skip", "this token may not use all of claim_task, post_progress, renew_lease and release_task");
    } else if (!writes) {
      record(`${who}: write flow on issue #${issue}`, "skip", "writes are off");
    } else {
      writers.push(entry);
      const claim = await call(header, "claim_task", sample("claim_task"));
      const held = claim?.envelope?.ok === true;
      expect(`${who}: claim_task claims issue #${issue}`, held, `got ${code(claim)}`);
      if (held) {
        try {
          const progress = await call(header, "post_progress", { issue_number: issue, done: `Smoke test as ${entry.agent}.`, next: "Release the lease." });
          expect(`${who}: post_progress succeeds`, progress?.envelope?.ok === true, `got ${code(progress)}`);
          const renew = await call(header, "renew_lease", sample("renew_lease"));
          expect(`${who}: renew_lease succeeds`, renew?.envelope?.ok === true, `got ${code(renew)}`);
        } finally {
          const release = await call(header, "release_task", { issue_number: issue, status: "ready", reason: `Smoke test finished for ${entry.agent}.` });
          expect(`${who}: release_task returns the issue to ready`, release?.envelope?.ok === true, `got ${code(release)}. Release issue #${issue} by hand.`);
        }
      }
    }

    // 3d. A review request, only against a test pull request.
    if (mayCall("request_review")) {
      if (options.pull === undefined || !writes) {
        record(`${who}: request_review on a test pull request`, "skip", options.pull === undefined ? "no --pull given" : "writes are off");
      } else if (reviewed) {
        // The Worker refuses a second request for the same head commit, so one token is enough.
        record(`${who}: request_review on a test pull request`, "skip", "already requested once for this head commit");
      } else {
        reviewed = true;
        const reply = await call(header, "request_review", { pull_number: options.pull });
        expect(`${who}: request_review on pull #${options.pull} succeeds`, reply?.envelope?.ok === true, `got ${code(reply)}`);
      }
    }
  }

  if (writers.length > 0) {
    cleanup.push(`Close test issue #${issue}. The Worker has no close tool, and the claim, progress and renewal comments stay on it.`);
  }
  if (options.pull !== undefined && writes) {
    cleanup.push(`Pull request #${options.pull} has a review-request comment from the Worker's GitHub App. Close that pull request if it was only a test.`);
  }

  const failed = checks.filter((check) => check.status === "fail").length;
  const skipped = checks.filter((check) => check.status === "skip").length;
  return { checks, passed: checks.length - failed - skipped, failed, skipped, ok: failed === 0, cleanup };
}
