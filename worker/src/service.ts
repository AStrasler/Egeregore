/**
 * The read tools: get_tasks, get_task, get_task_history, get_owner_decisions
 * and get_specialists. A port of the read paths in mcp/egregore_mcp/service.py
 * with the same envelope, error codes, messages and field order.
 *
 * Nothing here writes to GitHub, merges, deploys, changes settings, or records
 * an owner decision. This Worker has no way to know who is calling yet, so every
 * tool stays closed (`unauthenticated`) until a caller identity is supplied.
 */
import {
  ALLOWED_AGENTS,
  STALL_LABEL,
  assessLease,
  agentLine,
  author,
  classifyComment,
  decisionPlan,
  decisionVerdict,
  firstLine,
  isPullRequest,
  kindFromTitle,
  labelNames,
  redactText,
  sectionMap,
  stallReason,
  type Json,
  type LeaseState,
} from "./coordination";
import { GitHubError, SubrequestBudgetError, type GitHubReader } from "./github";
import { GitHubAppConfigError, GitHubAppScopeError } from "./github-app";
import { pyStr, pyStrip, stamp, textOrEmpty } from "./pycompat";
import { failure, getSpecialists, type Envelope } from "./specialists";

export type { Envelope } from "./specialists";

/** Issue-list pages one get_tasks or get_owner_decisions call may read (100 issues each). */
export const LIST_PAGES = 5;
/** Comment threads fetched at once. Workers allow six open connections. */
export const BATCH_SIZE = 5;

export interface ServiceConfig {
  /** The role on the Agent line. null means the caller is not authenticated. */
  agent: string | null;
  /** null when GitHub App credentials are not configured. */
  github: GitHubReader | null;
  configProblems: readonly string[];
  now: () => number;
}

const ok = (result: Json): Envelope => ({ ok: true, result, error: null });
export const err = (code: string, message: string, details: Json = {}): Envelope =>
  failure(code, message, details);

const nullable = (value: unknown): unknown => (value === undefined ? null : value);

export function leaseView(state: LeaseState): Json {
  return {
    status: state.status,
    agent: state.agent,
    expires: state.expires !== null ? stamp(state.expires) : null,
    lease_id: state.leaseId,
    marker: state.marker,
    conflicts: [...state.conflicts],
  };
}

/** Read-only mirror of the lease-watch stall gate. */
export function stallView(labels: readonly string[], state: LeaseState): Json {
  return {
    reason: stallReason(labels),
    flagged: labels.includes(STALL_LABEL),
    lease_blocks_flag: state.status === "active",
  };
}

function expectedRole(issue: Json): string | null {
  const parsed = sectionMap(textOrEmpty(issue.body));
  const kind = kindFromTitle(textOrEmpty(issue.title));
  let raw: string;
  if (kind === "handoff") {
    raw = firstLine(parsed.get("To"));
  } else if (kind === "task") {
    raw = firstLine(parsed.get("Role"));
  } else {
    return null;
  }
  if (!raw || raw === "_No response_") {
    return null;
  }
  return raw.toLowerCase();
}

function summary(issue: Json, lease: LeaseState): Json {
  const labels = labelNames(issue.labels || []);
  return {
    number: nullable(issue.number),
    title: nullable(issue.title),
    state: nullable(issue.state),
    html_url: nullable(issue.html_url),
    kind: kindFromTitle(textOrEmpty(issue.title)),
    labels,
    role: expectedRole(issue),
    lease: leaseView(lease),
    stall: stallView(labels, lease),
  };
}

interface TaskFilter {
  kind: string | null;
  status: string | null;
  project: string | null;
  role: string | null;
}

function matches(issue: Json, filter: TaskFilter): boolean {
  const titleKind = kindFromTitle(textOrEmpty(issue.title));
  if (filter.kind && filter.kind !== "all" && titleKind !== filter.kind) {
    return false;
  }
  const labels = labelNames(issue.labels || []);
  if (filter.status) {
    const wanted = filter.status.startsWith("status:") ? filter.status : `status:${filter.status}`;
    if (!labels.includes(wanted)) {
      return false;
    }
  }
  if (filter.project) {
    const wanted = filter.project.startsWith("project:") ? filter.project : `project:${filter.project}`;
    const section = firstLine(sectionMap(textOrEmpty(issue.body)).get("Project")).toLowerCase();
    const bare = wanted.startsWith("project:") ? wanted.slice("project:".length) : wanted;
    if (!labels.includes(wanted) && section !== filter.project.toLowerCase() && section !== bare) {
      return false;
    }
  }
  if (filter.role) {
    if (expectedRole(issue) !== pyStrip(filter.role).toLowerCase()) {
      return false;
    }
  }
  return true;
}

const STATES = new Set(["open", "closed", "all"]);
const KINDS = new Set(["", "all", "task", "handoff", "evidence", "review", "decision"]);

export class CoordinationService {
  constructor(private readonly config: ServiceConfig) {}

  async getTasks(args: {
    state?: string;
    kind?: string | null;
    status?: string | null;
    project?: string | null;
    role?: string | null;
    limit?: number;
  }): Promise<Envelope> {
    const { state = "open", kind = "task", status = null, project = null, role = null, limit = 30 } = args;
    const blocked = this.ready();
    if (blocked) return blocked;
    if (!STATES.has(state)) {
      return err("invalid_input", "state must be open, closed, or all.");
    }
    if (kind !== null && !KINDS.has(kind)) {
      return err("invalid_input", "kind must be task, handoff, evidence, review, decision, or all.");
    }
    const limitError = this.checkLimit(limit);
    if (limitError) return limitError;
    return this.guarded(async (github) => {
      const filter: TaskFilter = { kind, status, project, role };
      const listing = await github.listIssues(state, LIST_PAGES);
      const candidates = listing.items.filter((issue) => !isPullRequest(issue) && matches(issue, filter));
      const rows: Json[] = [];
      const collected = await this.withThreads(github, candidates, limit, (issue, comments) => {
        rows.push(summary(issue, assessLease(comments, this.config.now())));
      });
      return this.listResult(
        { tasks: rows, count: rows.length },
        collected.truncated,
        rows.length,
        limit,
        listing.truncated,
      );
    });
  }

  async getTask(issueNumber: number): Promise<Envelope> {
    return this.load(issueNumber, (issue, comments) => {
      const lease = assessLease(comments, this.config.now());
      const body = redactText(textOrEmpty(issue.body));
      return ok({
        issue: summary(issue, lease),
        body,
        sections: Object.fromEntries([...sectionMap(body)].map(([name, value]) => [name, redactText(value)])),
        lease: leaseView(lease),
        identity: this.identity(),
      });
    });
  }

  async getTaskHistory(issueNumber: number): Promise<Envelope> {
    return this.load(issueNumber, (issue, comments) => {
      const events = comments.map((comment) => {
        const body = textOrEmpty(comment.body);
        return {
          comment_id: "id" in comment ? pyStr(comment.id) : "",
          created_at: nullable(comment.created_at),
          author: author(comment),
          agent: agentLine(body),
          kind: classifyComment(body),
          body: redactText(body),
        };
      });
      const lease = assessLease(comments, this.config.now());
      return ok({
        issue_number: issueNumber,
        title: nullable(issue.title),
        events,
        lease: leaseView(lease),
        identity: this.identity(),
      });
    });
  }

  async getOwnerDecisions(args: { state?: string; limit?: number }): Promise<Envelope> {
    const { state = "open", limit = 30 } = args;
    const blocked = this.ready();
    if (blocked) return blocked;
    if (!STATES.has(state)) {
      return err("invalid_input", "state must be open, closed, or all.");
    }
    const limitError = this.checkLimit(limit);
    if (limitError) return limitError;
    return this.guarded(async (github) => {
      const listing = await github.listIssues(state, LIST_PAGES);
      const candidates = listing.items.filter((issue) => {
        if (isPullRequest(issue)) return false;
        const labels = labelNames(issue.labels || []);
        return kindFromTitle(textOrEmpty(issue.title)) === "decision" || labels.includes("type:decision");
      });
      const rows: Json[] = [];
      const collected = await this.withThreads(github, candidates, limit, (issue, comments) => {
        const labels = labelNames(issue.labels || []);
        const decisions: Json[] = [];
        for (const comment of comments) {
          const verdict = decisionVerdict(textOrEmpty(comment.body));
          if (verdict === null) continue;
          const who = author(comment);
          const plan = decisionPlan(who, verdict);
          decisions.push({
            comment_id: "id" in comment ? pyStr(comment.id) : "",
            author: who,
            verdict,
            authorization: plan !== null ? plan.authorization : "none",
            note: plan !== null ? plan.note : "",
          });
        }
        rows.push({
          number: Number(issue.number),
          title: nullable(issue.title),
          html_url: nullable(issue.html_url),
          labels,
          decisions,
        });
      });
      return this.listResult(
        {
          decisions: rows,
          count: rows.length,
          authorization_values: ["none", "recorded"],
          identity: this.identity(),
        },
        collected.truncated,
        rows.length,
        limit,
        listing.truncated,
      );
    });
  }

  getSpecialistCatalog(args: { role?: string; kind?: string; specialist_id?: string }): Envelope {
    const closed = this.authenticated();
    if (closed) return closed;
    return getSpecialists(args.role ?? "", args.kind ?? "", args.specialist_id ?? "");
  }

  // ---- internals -----------------------------------------------------------

  private authenticated(): Envelope | null {
    if (this.config.agent === null) {
      return err(
        "unauthenticated",
        "This endpoint has no caller authentication yet, so the coordination tools are closed.",
      );
    }
    return null;
  }

  private ready(): Envelope | null {
    const closed = this.authenticated();
    if (closed) return closed;
    if (this.config.configProblems.length > 0 || this.config.github === null) {
      return err(
        "unconfigured",
        "Set EGREGORE_APP_ID and EGREGORE_APP_PRIVATE_KEY before calling tools.",
        { missing: this.config.configProblems.length > 0 ? [...this.config.configProblems] : ["EGREGORE_APP_PRIVATE_KEY"] },
      );
    }
    if (!ALLOWED_AGENTS.has(this.config.agent!)) {
      return err("unconfigured", "The caller's agent role must be chatgpt, grokbot, cursor, codex, or aaron.");
    }
    return null;
  }

  private identity(): Json {
    return {
      agent: this.config.agent,
      auth: "github_app",
      github_login_is_separate: true,
      note:
        "Comments are attributed to the GitHub App bot, for example egregore[bot], " +
        "not to @AStrasler. The Agent line is the role, not a login, and it is not Aaron's approval.",
    };
  }

  private checkLimit(value: unknown): Envelope | null {
    if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 100) {
      return err("invalid_input", "limit must be an integer from 1 to 100.");
    }
    return null;
  }

  private checkNumber(value: unknown): Envelope | null {
    if (typeof value !== "number" || !Number.isInteger(value) || value < 1) {
      return err("invalid_input", "The issue or pull request number must be a positive integer.");
    }
    return null;
  }

  private async load(
    issueNumber: number,
    build: (issue: Json, comments: Json[]) => Envelope,
  ): Promise<Envelope> {
    const blocked = this.ready();
    if (blocked) return blocked;
    const invalid = this.checkNumber(issueNumber);
    if (invalid) return invalid;
    return this.guarded(async (github) => {
      const issue = await github.getIssue(issueNumber);
      const comments = await github.listComments(issueNumber);
      return build(issue, comments);
    });
  }

  /** Runs a read and turns every failure into an envelope. Never leaks a stack or a secret. */
  private async guarded(run: (github: GitHubReader) => Promise<Envelope>): Promise<Envelope> {
    try {
      return await run(this.config.github!);
    } catch (error) {
      return this.githubError(error);
    }
  }

  private githubError(error: unknown): Envelope {
    if (error instanceof SubrequestBudgetError) {
      return err("budget_exhausted", error.message, { status: 0 });
    }
    if (error instanceof GitHubError) {
      if (error.status === 404) {
        return err("not_found", "GitHub could not find that issue or pull request.", { status: 404 });
      }
      return err("github_error", error.message, { status: error.status });
    }
    if (error instanceof GitHubAppScopeError) {
      return err("scope_refused", error.message);
    }
    if (error instanceof GitHubAppConfigError) {
      return err("unconfigured", error.message);
    }
    return err("internal_error", "The coordination read failed unexpectedly.");
  }

  /**
   * Fetches comment threads in small concurrent batches, in issue order, and
   * hands each (issue, comments) to `visit` until `limit` rows exist. The first
   * failing thread in order fails the call, as in the Python server. Running out
   * of subrequests ends the walk early and reports truncated=true.
   */
  private async withThreads(
    github: GitHubReader,
    candidates: readonly Json[],
    limit: number,
    visit: (issue: Json, comments: Json[]) => void,
  ): Promise<{ truncated: boolean; visited: number }> {
    let visited = 0;
    let index = 0;
    while (visited < limit && index < candidates.length) {
      const room = Math.min(BATCH_SIZE, limit - visited, github.budget.remaining);
      if (room <= 0) {
        return { truncated: true, visited };
      }
      const batch = candidates.slice(index, index + room);
      index += batch.length;
      const settled = await Promise.allSettled(batch.map((issue) => github.listComments(Number(issue.number))));
      for (const [position, result] of settled.entries()) {
        if (result.status === "rejected") {
          if (result.reason instanceof SubrequestBudgetError) {
            return { truncated: true, visited };
          }
          throw result.reason;
        }
        visit(batch[position], result.value);
        visited += 1;
      }
    }
    return { truncated: false, visited };
  }

  private listResult(
    result: Json,
    threadsTruncated: boolean,
    rows: number,
    limit: number,
    listTruncated: boolean,
  ): Envelope {
    // A short list is incomplete when GitHub had more issues than this call may
    // read, or when the call ran out of outbound requests.
    const incomplete = threadsTruncated || (listTruncated && rows < limit);
    if (!incomplete) {
      return ok(result);
    }
    return ok({
      ...result,
      truncated: true,
      truncated_note:
        "This list is incomplete. A Cloudflare Worker may make only a few dozen GitHub calls per request. " +
        "Narrow the filters or lower the limit, then ask again.",
    });
  }
}
