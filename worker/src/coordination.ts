/**
 * Read-side port of scripts/coordination.py and the comment classifier in
 * mcp/egregore_mcp/service.py. The lease rules are the repo's single contract:
 * the earliest live claim holds, a later agent does not take an unexpired
 * lease, comment order is GitHub's receipt order, and the longest lease is 24
 * hours. This file never writes anything.
 *
 * tests/test_worker_parity_fixtures.py regenerates worker/test/parity/fixtures.json
 * from the Python code, and test/parity.test.ts requires this port to match it.
 */
import {
  PY_WS,
  WORD_END,
  WORD_START,
  pyStr,
  pyStrip,
  pySplitLines,
  splitNewlines,
  stamp,
  textOrEmpty,
  tryParseTime,
} from "./pycompat";

export type Json = Record<string, unknown>;

export const HYGIENE_MARKER = "<!-- egregore-hygiene -->";
export const ALLOWED_AGENTS: ReadonlySet<string> = new Set(["chatgpt", "grokbot", "cursor", "codex", "aaron"]);
export const OWNER_LOGIN = "AStrasler";
export const STALL_LABEL = "stall:flagged";
export const MAX_LEASE_MS = 24 * 60 * 60 * 1000;
export const BOUNDARY_SENTENCE =
  "Coordination only. No project data, health data, credentials, " +
  "or another person's personal information.";
export const TASK_ROLES: ReadonlySet<string> = new Set([...ALLOWED_AGENTS, "any"]);
export const STATUS_LABELS: readonly string[] = [
  "status:ready",
  "status:claimed",
  "status:in-progress",
  "status:blocked",
  "status:needs-review",
  "status:needs-owner",
  "status:done",
];
const PRIORITIES: ReadonlySet<string> = new Set(["low", "normal", "high", "urgent"]);
const RISKS: ReadonlySet<string> = new Set(["none", "auth or permissions", "data handling", "deployment", "other"]);
const EVIDENCE_RESULTS: ReadonlySet<string> = new Set(["progress", "blocked", "ready-for-review", "complete"]);
const DECISION_TYPES: ReadonlySet<string> = new Set([
  "merge",
  "deployment",
  "credentials",
  "permission or access",
  "new project",
  "repository setting",
  "other",
]);
const REQUIRED_SECTIONS: Readonly<Record<string, readonly string[]>> = {
  task: ["Project", "Role", "Priority", "Outcome", "Context", "Out of scope", "Approval needed"],
  handoff: ["Project", "From", "To", "Related issue", "Done so far", "Next action", "Access"],
  evidence: ["Project", "Related issue", "What happened", "Links", "Result"],
  review: ["Project", "Pull request", "What to verify", "Risk"],
  decision: ["Project", "Decision type", "Request", "Why Aaron"],
};

const D = "\\p{Nd}";
const SECRET_SOURCES: readonly string[] = [
  "gh[pousr]_[A-Za-z0-9]{20,}",
  "github_pat_[A-Za-z0-9_]{20,}",
  "-----BEGIN [A-Z ]*PRIVATE KEY-----",
  `${WORD_START}AKIA[0-9A-Z]{16}${WORD_END}`,
  `${WORD_START}xox[baprs]-[A-Za-z0-9-]{10,}`,
  `${WORD_START}sk-[A-Za-z0-9]{20,}`,
  `${WORD_START}npm_[A-Za-z0-9]{36}${WORD_END}`,
  `${WORD_START}AIza[0-9A-Za-z_-]{35}${WORD_END}`,
];

const HEADING_RE = new RegExp(`^###[${PY_WS}]+([^\\n]+?)[${PY_WS}]*$`, "u");
const EXPIRES_RE = new RegExp(
  `^Lease-Expires:[${PY_WS}]*(${D}{4}-${D}{2}-${D}{2}T${D}{2}:${D}{2}:${D}{2}Z)[${PY_WS}]*$`,
  "u",
);
const CLAIM_AGENT_RE = new RegExp(`^Claim-Agent:[${PY_WS}]*([A-Za-z]+)[${PY_WS}]*$`, "u");
const RELEASE_RE = new RegExp(`^Lease-Release:[${PY_WS}]*([A-Za-z]+)[${PY_WS}]*$`, "u");
const DECISION_RE = new RegExp(`^Decision:[${PY_WS}]*(approve|reject)${WORD_END}`, "iu");
const BOUNDARY_LINE_RE = new RegExp(`^Data-Boundary:[${PY_WS}]*coordination-only[${PY_WS}]*$`, "iu");
const CHECKED_BOX_RE = /\[[xX]\]/u;

export function looksLikeSecret(body: string): boolean {
  return SECRET_SOURCES.some((source) => new RegExp(source, "u").test(body));
}

export function redactText(text: string): string {
  let cleaned = text;
  for (const source of SECRET_SOURCES) {
    cleaned = cleaned.replace(new RegExp(source, "gu"), "[redacted]");
  }
  return cleaned;
}

export function labelNames(labels: unknown): string[] {
  const names: string[] = [];
  const list = Array.isArray(labels) ? labels : [];
  for (const label of list) {
    if (typeof label === "object" && label !== null && !Array.isArray(label)) {
      names.push("name" in label ? pyStr((label as Json).name) : "");
    } else {
      names.push(pyStr(label));
    }
  }
  return names;
}

export function isPullRequest(issue: Json): boolean {
  return "pull_request" in issue;
}

export function kindFromTitle(title: string): string | null {
  const folded = pyStrip(title).toLowerCase();
  const prefixes: ReadonlyArray<readonly [string, string]> = [
    ["[task]", "task"],
    ["[handoff]", "handoff"],
    ["[evidence]", "evidence"],
    ["[review]", "review"],
    ["[decision]", "decision"],
  ];
  for (const [prefix, kind] of prefixes) {
    if (folded.startsWith(prefix)) {
      return kind;
    }
  }
  return null;
}

/** Heading to text, in document order. A Map keeps order and allows any heading text. */
export function sectionMap(body: string): Map<string, string> {
  const found = new Map<string, string[]>();
  let current: string | null = null;
  for (const line of splitNewlines(body)) {
    const heading = HEADING_RE.exec(line);
    if (heading) {
      current = pyStrip(heading[1]);
      if (!found.has(current)) {
        found.set(current, []);
      }
      continue;
    }
    if (current !== null) {
      found.get(current)!.push(line);
    }
  }
  return new Map([...found].map(([name, lines]) => [name, pyStrip(lines.join("\n"))]));
}

export function firstLine(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  for (const line of pySplitLines(value)) {
    const stripped = pyStrip(line);
    if (stripped) {
      return stripped;
    }
  }
  return "";
}

export function decisionVerdict(body: string): string | null {
  for (const line of splitNewlines(body || "")) {
    const stripped = pyStrip(line);
    if (!stripped) {
      continue;
    }
    const match = DECISION_RE.exec(stripped);
    return match ? match[1].toLowerCase() : null;
  }
  return null;
}

export interface DecisionPlan {
  note: string;
  authorization: "none" | "recorded";
}

/** A Decision comment is a recorded note, never a verified authorization. */
export function decisionPlan(author: string, verdict: string | null): DecisionPlan | null {
  if (verdict === null) {
    return null;
  }
  if (author !== OWNER_LOGIN) {
    return {
      note:
        "This comment does not record an owner decision. The workflow did not change labels. " +
        "A GitHub login is not a verified authorization.",
      authorization: "none",
    };
  }
  return {
    note:
      "Recorded decision only. GitHub attributes this comment to @AStrasler. " +
      "That login is not proof Aaron personally approved, because an agent can post through his connected account. " +
      "This is not a verified authorization. The workflow did not change labels, access, or merge state. " +
      "Aaron verifies an owner action by doing it himself: merging, changing the setting, or moving the status label in a session he is driving.",
    authorization: "recorded",
  };
}

export function stallReason(labels: readonly string[]): string | null {
  const names = new Set(labels);
  if (names.has("status:done")) {
    return null;
  }
  const parts: string[] = [];
  if (names.has("status:needs-review")) {
    parts.push("needs-review");
  }
  if (names.has("type:handoff")) {
    parts.push("handoff");
  }
  if (names.has("status:needs-owner")) {
    parts.push("needs-owner");
  }
  return parts.length > 0 ? parts.join("+") : null;
}

export interface LeaseState {
  status: "active" | "expired" | "released" | "invalid_agent" | "too_long" | "missing";
  agent: string | null;
  /** Epoch milliseconds. */
  expires: number | null;
  marker: string;
  conflicts: string[];
  leaseId: string | null;
}

function holderIsLive(agent: string | null, expires: number | null, at: number): boolean {
  return Boolean(agent) && expires !== null && expires > at;
}

function withoutPreviousHolderConflicts(conflicts: string[]): string[] {
  return conflicts.filter((item) => !item.includes(" holds the lease"));
}

/**
 * The lease that is current after reading the thread. `comments` must be in
 * GitHub's receipt order. `nowMs` is epoch milliseconds.
 */
export function assessLease(comments: readonly Json[], nowMs: number): LeaseState {
  let holderAgent: string | null = null;
  let holderExpires: number | null = null;
  let leaseId: string | null = null;
  let releaseId = "none";
  let conflicts: string[] = [];
  let sawInvalid = false;
  let sawTooLong = false;
  let sawRelease = false;

  for (const [index, comment] of comments.entries()) {
    const created = tryParseTime(textOrEmpty(comment.created_at));
    const commentId = "id" in comment ? pyStr(comment.id) : String(index);
    if (created === null) {
      sawInvalid = true;
      conflicts.push("ignored comment with an invalid timestamp");
      continue;
    }
    const body = textOrEmpty(comment.body);
    // An explicit Claim-Agent applies only inside this comment. A renewal that
    // omits one belongs to the accepted holder, not to a rejected claim.
    let declared: string | null = null;
    for (const raw of pySplitLines(body)) {
      const line = pyStrip(raw);
      const claim = CLAIM_AGENT_RE.exec(line);
      if (claim) {
        declared = claim[1].toLowerCase();
        continue;
      }
      const release = RELEASE_RE.exec(line);
      if (release) {
        const who = release[1].toLowerCase();
        if (holderIsLive(holderAgent, holderExpires, created) && who !== holderAgent) {
          conflicts.push(`ignored release by ${who} while ${holderAgent} holds the lease`);
          continue;
        }
        holderAgent = null;
        holderExpires = null;
        leaseId = null;
        releaseId = commentId;
        sawRelease = true;
        conflicts = withoutPreviousHolderConflicts(conflicts);
        continue;
      }
      const expires = EXPIRES_RE.exec(line);
      if (!expires) {
        continue;
      }
      const exp = tryParseTime(expires[1]);
      if (exp === null) {
        sawInvalid = true;
        conflicts.push("ignored lease with an invalid timestamp");
        continue;
      }
      const agent: string | null = declared !== null ? declared : holderAgent;
      if (agent === null || !ALLOWED_AGENTS.has(agent)) {
        sawInvalid = true;
        conflicts.push("ignored lease with no known Claim-Agent");
        continue;
      }
      // A different agent does not take a live lease, even with a term longer
      // than 24 hours. Length is checked only for a claim that would otherwise
      // become the hold.
      if (holderIsLive(holderAgent, holderExpires, created) && agent !== holderAgent) {
        conflicts.push(`ignored overlapping claim by ${agent} while ${holderAgent} holds the lease`);
        continue;
      }
      if (exp - created > MAX_LEASE_MS) {
        sawTooLong = true;
        conflicts.push(`ignored lease by ${agent} longer than 24 hours`);
        continue;
      }
      // A same-agent renewal of an unexpired lease keeps that claim's id and
      // its warnings. Any other accepted claim is a new hold.
      const liveRenewal = holderIsLive(holderAgent, holderExpires, created) && agent === holderAgent;
      if (!liveRenewal) {
        conflicts = withoutPreviousHolderConflicts(conflicts);
        leaseId = commentId;
      }
      holderAgent = agent;
      holderExpires = exp;
      sawRelease = false;
    }
  }

  if (holderAgent && holderExpires !== null) {
    const when = stamp(holderExpires);
    const suffix = conflicts.length > 0 ? `:c${conflicts.length}` : "";
    const identity = leaseId || "none";
    if (holderExpires <= nowMs) {
      return {
        status: "expired",
        agent: holderAgent,
        expires: holderExpires,
        marker: `expired:${identity}:${when}${suffix}`,
        conflicts,
        leaseId,
      };
    }
    return {
      status: "active",
      agent: holderAgent,
      expires: holderExpires,
      marker: `active:${identity}:${when}${suffix}`,
      conflicts,
      leaseId,
    };
  }
  const base = { agent: null, expires: null, conflicts, leaseId: null };
  if (sawRelease) {
    return { ...base, status: "released", marker: `released:${releaseId}` };
  }
  if (sawInvalid && !sawTooLong) {
    return { ...base, status: "invalid_agent", marker: "invalid:none" };
  }
  if (sawTooLong) {
    return { ...base, status: "too_long", marker: "too_long:none" };
  }
  return { ...base, status: "missing", marker: "missing:none" };
}

// ---- Comment classification (mcp/egregore_mcp/service.py _classify, _agent_line) ----

export function author(comment: Json): string {
  const user = comment.user;
  if (typeof user === "object" && user !== null && !Array.isArray(user)) {
    return textOrEmpty((user as Json).login);
  }
  return "";
}

export function classifyComment(body: string): string {
  const lines = splitNewlines(body).map(pyStrip);
  const has = (test: (line: string) => boolean) => lines.some(test);
  if (has((l) => l.includes("egregore-lease-conflict:"))) return "lease_conflict";
  if (has((l) => l.startsWith("<!-- egregore-lease:"))) return "lease_backstop";
  if (has((l) => l.includes("<!-- egregore-stall:"))) return "stall";
  if (has((l) => l.includes(HYGIENE_MARKER))) return "hygiene";
  if (has((l) => l.startsWith("<!-- egregore-decision:"))) return "decision_note";
  if (decisionVerdict(body)) return "decision";
  // A handoff comment also carries Lease-Release. The heading is the event.
  if (has((l) => l === "## Handoff")) return "handoff";
  if (has((l) => RELEASE_RE.test(l))) return "release";
  const hasExpiry = has((l) => EXPIRES_RE.test(l));
  if (hasExpiry && has((l) => l === "## Lease renewal")) return "renewal";
  if (hasExpiry && has((l) => CLAIM_AGENT_RE.test(l))) return "claim";
  if (hasExpiry) return "renewal";
  if (has((l) => l === "## Progress")) return "progress";
  if (has((l) => l === "## Evidence")) return "evidence";
  return "comment";
}

export function agentLine(body: string): string | null {
  for (const raw of splitNewlines(body)) {
    const stripped = pyStrip(raw);
    if (stripped.toLowerCase().startsWith("agent:")) {
      return pyStrip(stripped.slice(stripped.indexOf(":") + 1)) || null;
    }
  }
  return null;
}

// ---- Field check (scripts/coordination.py validate_issue) ----

export interface IssueCheck {
  ok: boolean;
  kind: string | null;
  problems: string[];
  needsOwner: boolean;
  secret: boolean;
}

function present(value: string | null | undefined): boolean {
  const text = pyStrip(value ?? "");
  return text !== "" && text !== "_No response_";
}

/** checked, unchecked, or missing. */
function boundaryState(body: string): "checked" | "unchecked" | "missing" {
  let sawSentence = false;
  for (const line of splitNewlines(body)) {
    if (BOUNDARY_LINE_RE.test(pyStrip(line))) {
      return "checked";
    }
    if (line.includes(BOUNDARY_SENTENCE)) {
      sawSentence = true;
      if (CHECKED_BOX_RE.test(line)) {
        return "checked";
      }
    }
  }
  return sawSentence ? "unchecked" : "missing";
}

export function validateIssue(title: string, body: string): IssueCheck {
  const text = body || "";
  const kind = kindFromTitle(title || "");
  const parsed = sectionMap(text);
  const problems: string[] = [];
  let needsOwner = false;

  const boundary = boundaryState(text);
  if (boundary === "missing") {
    problems.push("Data boundary confirmation is missing.");
  } else if (boundary === "unchecked") {
    problems.push("Data boundary checkbox is unchecked.");
  }

  if (looksLikeSecret(text)) {
    problems.push(
      "A credential-like string is present. Remove it and rotate the credential. " +
        "The value was not copied into this comment. This repository is public, so a " +
        "saved value may already be exposed.",
    );
    needsOwner = true;
  }

  if (kind === null) {
    problems.push("Title must start with [Task]:, [Handoff]:, [Evidence]:, [Review]:, or [Decision]:.");
  } else {
    for (const heading of REQUIRED_SECTIONS[kind]) {
      if (!present(parsed.get(heading))) {
        problems.push(`${heading} is empty.`);
      }
    }
  }

  const project = firstLine(parsed.get("Project"));
  if (project === "other" && !present(parsed.get("Project slug"))) {
    problems.push("Project slug is required when Project is other.");
  }

  if (kind === "task" && present(parsed.get("Role"))) {
    const role = firstLine(parsed.get("Role")).toLowerCase();
    if (!TASK_ROLES.has(role)) {
      problems.push("Role must be chatgpt, grokbot, cursor, codex, aaron, or any.");
    }
  }
  if (kind === "task" && present(parsed.get("Priority"))) {
    const priority = firstLine(parsed.get("Priority")).toLowerCase();
    if (!PRIORITIES.has(priority)) {
      problems.push("Priority must be low, normal, high, or urgent.");
    }
  }
  if (kind === "task" && present(parsed.get("Approval needed"))) {
    const approval = firstLine(parsed.get("Approval needed")).toLowerCase();
    if (approval.startsWith("yes")) {
      needsOwner = true;
    } else if (!approval.startsWith("no")) {
      problems.push("Approval needed must start with yes or no.");
    }
  }

  if (kind === "handoff") {
    for (const heading of ["From", "To"]) {
      if (!present(parsed.get(heading))) {
        continue;
      }
      const agent = firstLine(parsed.get(heading)).toLowerCase();
      if (!ALLOWED_AGENTS.has(agent)) {
        problems.push(`${heading} must name chatgpt, grokbot, cursor, codex, or aaron.`);
      }
    }
    const access = parsed.get("Access") || "";
    if (present(access) && !access.toLowerCase().includes("does not grant")) {
      problems.push("Access must say that the handoff does not grant access.");
    }
  }

  if (kind === "review" && present(parsed.get("Risk"))) {
    const risk = firstLine(parsed.get("Risk")).toLowerCase();
    if (!RISKS.has(risk)) {
      problems.push("Risk must be none, auth or permissions, data handling, deployment, or other.");
    }
  }

  if (kind === "evidence" && present(parsed.get("Result"))) {
    const result = firstLine(parsed.get("Result")).toLowerCase();
    if (!EVIDENCE_RESULTS.has(result)) {
      problems.push("Result must be progress, blocked, ready-for-review, or complete.");
    }
  }

  if (kind === "decision") {
    needsOwner = true;
    if (present(parsed.get("Decision type"))) {
      const decisionType = firstLine(parsed.get("Decision type")).toLowerCase();
      if (!DECISION_TYPES.has(decisionType)) {
        problems.push("Decision type is not a known value.");
      }
    }
  }

  return { ok: problems.length === 0, kind, problems, needsOwner, secret: looksLikeSecret(text) };
}

// ---- Overlapping-claim note (scripts/coordination.py lease_conflict_*) ----

export function currentLeaseConflicts(conflicts: readonly string[]): string[] {
  return conflicts.filter((item) => item.includes(" holds the lease"));
}

/** Dedup key for one hold. Renewals keep the claim id; a later hold does not. */
export function leaseConflictMarker(state: LeaseState): string {
  const relevant = currentLeaseConflicts(state.conflicts);
  return `<!-- egregore-lease-conflict:${state.leaseId || "none"}:${state.agent ?? ""}:${relevant.join(" | ")} -->`;
}

/** True when this hold's overlap is not already noted in the thread. */
export function conflictCommentNeeded(comments: readonly Json[], state: LeaseState): boolean {
  if (state.status !== "active" || currentLeaseConflicts(state.conflicts).length === 0) {
    return false;
  }
  const marker = leaseConflictMarker(state);
  return !comments.some((comment) => textOrEmpty(comment.body).includes(marker));
}

export function leaseConflictComment(state: LeaseState): string {
  const when = state.expires !== null ? stamp(state.expires) : "unknown";
  const lines = [
    leaseConflictMarker(state),
    "",
    `Overlapping claim ignored. ${state.agent === null ? "None" : state.agent} still holds the lease until ${when}.`,
    "GitHub comments are not a lock. A later claim does not replace an earlier live lease.",
    "The later agent stops. Do not open a second branch or pull request for this task.",
  ];
  for (const item of currentLeaseConflicts(state.conflicts)) {
    lines.push(`- ${item}`);
  }
  lines.push("", "This workflow does not claim the issue.");
  return lines.join("\n") + "\n";
}
