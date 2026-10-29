/**
 * Per-agent bearer-token authentication and the permission model.
 *
 * Tokens are never stored. The Worker secret `EGREGORE_AGENT_TOKENS` holds
 * their SHA-256 digests with the agent, permissions, repositories and tools
 * each token may use. Identity comes from the matching record and from nothing
 * else: no tool takes an `agent` argument.
 *
 * Everything here fails closed. A missing, malformed or partly invalid config
 * authenticates nobody. A tool that has no permission mapped is refused. A
 * principal with an empty list gets nothing.
 *
 * The permission model is written for this Worker. Its rules follow the shape
 * of `egregore_runtime/policy.py` and `egregore_runtime/contracts.py` in
 * egregore-labs/egregore at d570d10 (MIT): a closed permission set, a deny
 * when there is no grant, and path checks that reject empty, `.` and `..`
 * segments. See THIRD_PARTY_NOTICES.md.
 *
 * This file imports nothing and uses only syntax that Node can strip, so
 * `scripts/check-agent-tokens.mjs` can load the same code the Worker runs.
 */

export type Permission = "read" | "write" | "review";

export const PERMISSIONS: readonly Permission[] = ["read", "write", "review"];

/** Agents a token may act as. `aaron` is a handoff target, never a caller. */
export const TOKEN_AGENTS: readonly string[] = ["chatgpt", "grokbot", "cursor", "codex"];

/** The permission each tool needs. A tool missing from this table is refused. */
export const TOOL_PERMISSIONS: Readonly<Record<string, Permission>> = {
  ping: "read",
  get_tasks: "read",
  get_task: "read",
  get_task_history: "read",
  get_owner_decisions: "read",
  get_specialists: "read",
  claim_task: "write",
  renew_lease: "write",
  release_task: "write",
  post_progress: "write",
  handoff_task: "write",
  request_review: "review",
};

export const MAX_CONFIG_BYTES = 16 * 1024;
export const MAX_RECORDS = 32;
export const MAX_REPOS_PER_RECORD = 16;
const MIN_TOKEN_LENGTH = 32;
const MAX_TOKEN_LENGTH = 256;

export interface Principal {
  agent: string;
  permissions: ReadonlySet<Permission>;
  /** Lower-case `owner/name`. */
  repos: ReadonlySet<string>;
  /** `null` means every tool the permissions allow. */
  tools: ReadonlySet<string> | null;
}

interface TokenRecord {
  digest: Uint8Array;
  principal: Principal;
}

export type TokenConfig =
  | { ok: true; records: readonly TokenRecord[] }
  | { ok: false; problems: readonly string[] };

export type Decision =
  | { allowed: true }
  | { allowed: false; code: "unauthenticated" | "forbidden"; message: string; details: Record<string, unknown> };

// ---------------------------------------------------------------- safe paths

const SEGMENT_RE = /^[A-Za-z0-9_.-]{1,100}$/;

/**
 * Split a `/` path into segments, or return null. Strict on purpose: no
 * leading or trailing slash, no empty segment, no `.` or `..` (or any segment
 * made only of dots), no backslash, percent escape or control character. It
 * does not clean the input up, because a lenient parser is how `a/../b` slips
 * through.
 */
export function safeSegments(path: unknown): string[] | null {
  if (typeof path !== "string" || path.length === 0 || path.length > 200) return null;
  const parts = path.split("/");
  for (const part of parts) {
    if (!SEGMENT_RE.test(part)) return null;
    if (/^\.+$/.test(part)) return null;
  }
  return parts;
}

/** `owner/name`, or null. */
export function safeRepo(value: unknown): string | null {
  const parts = safeSegments(value);
  if (parts === null || parts.length !== 2) return null;
  return `${parts[0]}/${parts[1]}`;
}

/** Repository names compare case-insensitively on GitHub. */
export function repoKey(repo: string): string {
  return repo.toLowerCase();
}

// ------------------------------------------------------------- hashing

const encoder = new TextEncoder();

export async function sha256(text: string): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(text)));
}

export function toHex(bytes: Uint8Array): string {
  let out = "";
  for (const byte of bytes) out += byte.toString(16).padStart(2, "0");
  return out;
}

function fromHex(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let index = 0; index < out.length; index += 1) out[index] = parseInt(hex.slice(index * 2, index * 2 + 2), 16);
  return out;
}

/**
 * Compare two equal-length digests without stopping at the first difference.
 * Both inputs are SHA-256 outputs, so the length is public.
 */
export function constantTimeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) difference |= a[index] ^ b[index];
  return difference === 0;
}

// ------------------------------------------------------------- config

const RECORD_KEYS = new Set(["sha256", "agent", "permissions", "repos", "tools", "label"]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Parse the `EGREGORE_AGENT_TOKENS` value. Any problem rejects the whole
 * config: a typo must not leave one token working with broader access than
 * intended. The problems name records by position and never echo a value.
 */
export function parseTokenConfig(raw: unknown): TokenConfig {
  if (typeof raw !== "string" || raw.trim() === "") return { ok: false, problems: ["EGREGORE_AGENT_TOKENS is not set"] };
  if (encoder.encode(raw).length > MAX_CONFIG_BYTES) {
    return { ok: false, problems: [`EGREGORE_AGENT_TOKENS is larger than ${MAX_CONFIG_BYTES} bytes`] };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, problems: ["EGREGORE_AGENT_TOKENS is not valid JSON"] };
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    return { ok: false, problems: ["EGREGORE_AGENT_TOKENS must be a non-empty JSON array"] };
  }
  if (parsed.length > MAX_RECORDS) {
    return { ok: false, problems: [`EGREGORE_AGENT_TOKENS has more than ${MAX_RECORDS} records`] };
  }
  const problems: string[] = [];
  const records: TokenRecord[] = [];
  const seen = new Set<string>();
  parsed.forEach((entry, position) => {
    const where = `record ${position}`;
    if (!isPlainObject(entry)) {
      problems.push(`${where} must be an object`);
      return;
    }
    for (const key of Object.keys(entry)) {
      if (!RECORD_KEYS.has(key)) problems.push(`${where} has an unknown key`);
    }
    const hash = entry.sha256;
    let digest: Uint8Array | null = null;
    if (typeof hash !== "string" || !/^[0-9a-fA-F]{64}$/.test(hash)) {
      problems.push(`${where} needs sha256 as 64 hex characters`);
    } else {
      const normal = hash.toLowerCase();
      if (seen.has(normal)) problems.push(`${where} repeats another record's sha256`);
      seen.add(normal);
      digest = fromHex(normal);
    }
    const agent = entry.agent;
    if (typeof agent !== "string" || !TOKEN_AGENTS.includes(agent)) {
      problems.push(`${where} needs agent to be one of ${TOKEN_AGENTS.join(", ")}`);
    }
    const permissions = stringList(entry.permissions);
    if (permissions === null || permissions.length === 0 || !permissions.every((item) => PERMISSIONS.includes(item as Permission))) {
      problems.push(`${where} needs permissions from ${PERMISSIONS.join(", ")}`);
    }
    const repoList = stringList(entry.repos);
    const repos = new Set<string>();
    if (repoList === null || repoList.length === 0 || repoList.length > MAX_REPOS_PER_RECORD) {
      problems.push(`${where} needs 1 to ${MAX_REPOS_PER_RECORD} repos`);
    } else {
      for (const repo of repoList) {
        const safe = safeRepo(repo);
        if (safe === null) problems.push(`${where} has a repo that is not a plain owner/name`);
        else repos.add(repoKey(safe));
      }
    }
    const toolList = stringList(entry.tools);
    let tools: ReadonlySet<string> | null = null;
    if (toolList === null || toolList.length === 0) {
      problems.push(`${where} needs tools: ["*"] or a list of tool names`);
    } else if (toolList.includes("*")) {
      if (toolList.length !== 1) problems.push(`${where} mixes "*" with tool names`);
    } else if (!toolList.every((name) => Object.hasOwn(TOOL_PERMISSIONS, name))) {
      problems.push(`${where} lists a tool this server does not have`);
    } else {
      tools = new Set(toolList);
    }
    if (entry.label !== undefined && (typeof entry.label !== "string" || entry.label.length > 80)) {
      problems.push(`${where} has a label that is not a string of 80 characters or fewer`);
    }
    if (digest !== null && typeof agent === "string" && permissions !== null) {
      records.push({
        digest,
        principal: {
          agent,
          permissions: new Set(permissions as Permission[]),
          repos,
          tools,
        },
      });
    }
  });
  if (problems.length > 0) return { ok: false, problems };
  return { ok: true, records };
}

function stringList(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.every((item) => typeof item === "string") ? (value as string[]) : null;
}

// ------------------------------------------------------------- tokens

/**
 * The bearer token in an `Authorization` header, or null. One header, the
 * `Bearer` scheme, and a bounded printable token. The length cap runs before
 * any hashing so an oversized header costs nothing.
 */
export function bearerToken(header: string | null): string | null {
  if (header === null) return null;
  const match = /^Bearer ([A-Za-z0-9._~+/=-]+)$/i.exec(header);
  if (match === null) return null;
  const token = match[1];
  if (token.length < MIN_TOKEN_LENGTH || token.length > MAX_TOKEN_LENGTH) return null;
  return token;
}

/**
 * Look the token up. It is hashed once, then compared against every stored
 * digest with no early exit, so the time does not depend on which record, if
 * any, matches.
 */
export async function authenticateToken(token: string | null, config: TokenConfig): Promise<Principal | null> {
  if (token === null || !config.ok) return null;
  const digest = await sha256(token);
  let match: Principal | null = null;
  for (const record of config.records) {
    if (constantTimeEqual(digest, record.digest)) match = record.principal;
  }
  return match;
}

let cachedRaw: string | undefined;
let cachedConfig: TokenConfig | undefined;

/** Parse once per isolate. The cache key is the secret text itself. */
export function loadTokenConfig(raw: unknown): TokenConfig {
  if (typeof raw === "string" && raw === cachedRaw && cachedConfig !== undefined) return cachedConfig;
  const config = parseTokenConfig(raw);
  if (typeof raw === "string") {
    cachedRaw = raw;
    cachedConfig = config;
  }
  return config;
}

/** The default `authenticate` for the Worker: bearer token against `EGREGORE_AGENT_TOKENS`. */
export async function authenticateRequest(
  request: { headers: { get(name: string): string | null } },
  env: { EGREGORE_AGENT_TOKENS?: unknown },
): Promise<Principal | null> {
  return authenticateToken(bearerToken(request.headers.get("authorization")), loadTokenConfig(env.EGREGORE_AGENT_TOKENS));
}

// ------------------------------------------------------------- decisions

/**
 * May this principal call this tool against this repository? The order is
 * fixed: identity, tool allow-list, permission, repository allow-list.
 */
export function decide(principal: Principal | null, tool: string, repo: string): Decision {
  if (principal === null) {
    return {
      allowed: false,
      code: "unauthenticated",
      message: "Send a valid bearer token in the Authorization header. This server does not say which part failed.",
      details: {},
    };
  }
  const needed = Object.hasOwn(TOOL_PERMISSIONS, tool) ? TOOL_PERMISSIONS[tool] : undefined;
  if (needed === undefined) {
    return forbidden(`The server has no permission mapped for ${tool}, so it is refused.`, { tool });
  }
  if (principal.tools !== null && !principal.tools.has(tool)) {
    return forbidden(`This token may not call ${tool}.`, { tool, reason: "tool_not_allowed" });
  }
  if (!principal.permissions.has(needed)) {
    return forbidden(`This token lacks the ${needed} permission that ${tool} needs.`, {
      tool,
      reason: "permission_missing",
      required: needed,
    });
  }
  const safe = safeRepo(repo);
  if (safe === null) {
    return forbidden("The server's EGREGORE_REPO is not a plain owner/name, so it is refused.", {
      tool,
      reason: "repo_invalid",
    });
  }
  if (!principal.repos.has(repoKey(safe))) {
    return forbidden("This token may not use the configured repository.", { tool, reason: "repo_not_allowed" });
  }
  return { allowed: true };
}

function forbidden(message: string, details: Record<string, unknown>): Decision {
  return { allowed: false, code: "forbidden", message, details };
}
