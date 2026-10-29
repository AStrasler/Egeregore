/**
 * The coordination tools. Reads: get_tasks, get_task, get_task_history,
 * get_owner_decisions and get_specialists. Writes: claim_task, renew_lease,
 * release_task, post_progress, handoff_task and request_review. A port of
 * mcp/egregore_mcp/service.py with the same envelope, error codes, messages,
 * field order and, for writes, the same sequence of GitHub calls.
 *
 * The writes comment, label and open handoff issues. They never merge, deploy,
 * change settings, or record an owner decision. This Worker has no way to know
 * who is calling yet, so every tool stays closed (`unauthenticated`) until a
 * caller identity is supplied.
 */
import {
  ALLOWED_AGENTS,
  BOUNDARY_SENTENCE,
  MAX_LEASE_MS,
  STALL_LABEL,
  STATUS_LABELS,
  assessLease,
  agentLine,
  author,
  classifyComment,
  conflictCommentNeeded,
  decisionPlan,
  decisionVerdict,
  firstLine,
  isPullRequest,
  kindFromTitle,
  labelNames,
  leaseConflictComment,
  leaseConflictMarker,
  looksLikeSecret,
  redactText,
  sectionMap,
  stallReason,
  validateIssue,
  type Json,
  type LeaseState,
} from "./coordination";
import {
  REVIEW_COMMENT,
  claimComment,
  conflictComment,
  handoffIssueBody,
  handoffNote,
  progressComment,
  releaseComment,
  renewalComment,
  workRepository,
} from "./comments";
import { DEFAULT_REPO, GitHubError, SubrequestBudgetError, type GitHubReader, type GitHubWriter } from "./github";
import { GitHubAppConfigError, GitHubAppScopeError } from "./github-app";
import { pyHead, pyLen, pyStr, pyStrip, stamp, textOrEmpty } from "./pycompat";
import {
  MAX_REVIEW_ROUNDS,
  SHA_RE,
  completedReviews,
  reviewBlock,
  reviewPassed,
} from "./reviews";
import { isEvidenceIssueFor, linkedPullNumbers, needsOwner, pullRequirement } from "./rules";
import { failure, getSpecialists, type Envelope } from "./specialists";

export type { Envelope } from "./specialists";

/** Issue-list pages one get_tasks or get_owner_decisions call may read (100 issues each). */
export const LIST_PAGES = 5;
/** Comment threads fetched at once. Workers allow six open connections. */
export const BATCH_SIZE = 5;

const MAX_LIST_PAGES = 10;

export interface ServiceConfig {
  /** The role on the Agent line. null means the caller is not authenticated. */
  agent: string | null;
  /** null when GitHub App credentials are not configured. */
  github: GitHubWriter | null;
  configProblems: readonly string[];
  now: () => number;
  /** owner/name of the coordination repository. Pull links elsewhere cannot be verified. */
  repo?: string;
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

/**
 * An upper bound on the calls setStatus makes for an issue: one DELETE for each
 * label it could remove, one POST to add, and two spare for a label that
 * appears between the read and the write.
 */
export function statusCalls(issue: Json): number {
  const removable = labelNames(issue.labels || []).filter(
    (name) => STATUS_LABELS.includes(name) || name === "lease:expired" || name.startsWith("agent:"),
  );
  return removable.length + 1 + 2;
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

/** One unit for a token refresh that lands mid-call. A refresh takes at most three. */
export const MINT_SLACK = 3;
export const TEXT_LIMIT = 4000;
const RELEASE_STATUSES: ReadonlySet<string> = new Set([
  "status:ready",
  "status:blocked",
  "status:needs-review",
  "status:needs-owner",
  "status:done",
]);

/** Ends a tool call early with the envelope to return. Mirrors Python's early `return err(...)`. */
class Stop {
  constructor(readonly envelope: Envelope) {}
}
const stop = (envelope: Envelope): Stop => new Stop(envelope);

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

  // ---- write tools ---------------------------------------------------------
  //
  // Each follows the Python server's order of reads and writes step for step.
  // Before the first write, `reserve` checks that the rest of the call fits in
  // this request's subrequest budget, so a large thread is refused up front and
  // not half-applied.

  async claimTask(issueNumber: number, leaseHours = 6): Promise<Envelope> {
    return this.mutation(async (github) => {
      const { issue, comments, pages } = await this.loadPair(github, issueNumber);
      const hours = this.checkHours(leaseHours);
      const state = assessLease(comments, this.config.now());
      const refusal = this.claimRefusal(issue, state);
      if (refusal) return refusal;
      if (state.status === "active" && state.agent === this.config.agent) {
        return ok({
          issue_number: issueNumber,
          held: true,
          already_held: true,
          lease: leaseView(state),
          identity: this.identity(),
        });
      }
      // Comment, a fresh read of the thread, an overlap note, then the labels.
      this.reserve(github, 1 + (pages + 1) + 1 + statusCalls(issue), "claim_task");
      if (state.status === "active" && state.agent !== this.config.agent) {
        return this.losingClaim(github, issueNumber, issue, hours, state.agent);
      }
      return this.winningClaim(github, issueNumber, issue, hours);
    });
  }

  async renewLease(issueNumber: number, leaseHours = 6, note = ""): Promise<Envelope> {
    return this.mutation(async (github) => {
      const { state, pages } = await this.holder(github, issueNumber);
      const hours = this.checkHours(leaseHours);
      this.checkOptionalText(note, "note");
      if (state.status !== "active" || state.agent !== this.config.agent) {
        return this.notHolding(state);
      }
      await this.guardMutation(github, issueNumber);
      this.reserve(github, 1 + (pages + 1), "renew_lease");
      const expires = stamp(this.expiry(hours));
      const posted = await github.createComment(
        issueNumber,
        renewalComment({ agent: this.agentRole, expires, note }),
      );
      const fresh = await this.assess(github, issueNumber);
      return ok({
        issue_number: issueNumber,
        comment_id: pyStr(posted.id),
        lease: leaseView(fresh),
        identity: this.identity(),
      });
    });
  }

  async releaseTask(issueNumber: number, status: string, reason: string): Promise<Envelope> {
    return this.mutation(async (github) => {
      const { issue, comments, pages } = await this.loadPair(github, issueNumber);
      const label = status.startsWith("status:") ? status : `status:${status}`;
      if (!RELEASE_STATUSES.has(label)) {
        return err("invalid_input", "status must be ready, blocked, needs-review, needs-owner, or done.");
      }
      this.checkRequiredText(reason, "reason");
      const labels = labelNames(issue.labels || []);
      if (needsOwner(issue) && !labels.includes("status:needs-owner")) {
        return err("not_claimable", "This issue is waiting on the owner. release_task did not post.");
      }
      const state = assessLease(comments, this.config.now());
      const holder = state.status === "active" && state.agent === this.config.agent;
      const lastHolder = state.status === "expired" && state.agent === this.config.agent;
      if (!holder && !lastHolder) {
        return this.notHolding(state);
      }
      let fresh = await this.guardMutation(github, issueNumber);
      if (label === "status:done") {
        const doneRefusal = await this.doneRefusal(github, fresh, comments);
        if (doneRefusal) return doneRefusal;
        fresh = await this.guardMutation(github, issueNumber);
      }
      // Comment, a re-read, the labels, then a fresh read of the thread.
      this.reserve(github, 1 + 1 + statusCalls(fresh) + (pages + 1), "release_task");
      const posted = await github.createComment(
        issueNumber,
        releaseComment({ agent: this.agentRole, reason }),
      );
      const current = await this.guardMutation(github, issueNumber);
      await this.setStatus(github, issueNumber, current, label, this.agentRole, true);
      const after = await this.assess(github, issueNumber);
      return ok({
        issue_number: issueNumber,
        comment_id: pyStr(posted.id),
        status: label,
        lease: leaseView(after),
        identity: this.identity(),
      });
    });
  }

  async postProgress(issueNumber: number, done: string, nextStep: string): Promise<Envelope> {
    return this.mutation(async (github) => {
      await this.holder(github, issueNumber);
      this.checkRequiredText(done, "done");
      this.checkRequiredText(nextStep, "next");
      const fresh = await this.guardMutation(github, issueNumber);
      // Comment, a re-read, then at most the labels.
      this.reserve(github, 1 + 1 + statusCalls(fresh), "post_progress");
      const posted = await github.createComment(
        issueNumber,
        progressComment({ agent: this.agentRole, done, nextStep }),
      );
      const current = await this.guardMutation(github, issueNumber);
      const labels = labelNames(current.labels || []);
      if (labels.includes("status:claimed") && !labels.includes("status:needs-owner")) {
        await this.setStatus(github, issueNumber, current, "status:in-progress", this.agentRole, true);
      }
      return ok({
        issue_number: issueNumber,
        comment_id: pyStr(posted.id),
        identity: this.identity(),
      });
    });
  }

  async handoffTask(
    issueNumber: number,
    toAgent: string,
    doneSoFar: string,
    nextAction: string,
    title = "",
  ): Promise<Envelope> {
    return this.mutation(async (github) => {
      await this.holder(github, issueNumber);
      const target = pyStrip(toAgent).toLowerCase();
      if (!ALLOWED_AGENTS.has(target)) {
        return err("invalid_input", "to_agent must be chatgpt, grokbot, cursor, codex, or aaron.");
      }
      if (target === this.config.agent) {
        return err("invalid_input", "A handoff goes to a different role. Keep working on this issue instead.");
      }
      this.checkRequiredText(doneSoFar, "done_so_far");
      this.checkRequiredText(nextAction, "next_action");
      this.checkOptionalText(title, "title");
      const issue = await this.guardMutation(github, issueNumber);
      const parsed = sectionMap(textOrEmpty(issue.body));
      const project = firstLine(parsed.get("Project")) || "coordination";
      let projectSlug = firstLine(parsed.get("Project slug"));
      if (projectSlug === "_No response_") {
        projectSlug = "";
      }
      const body = handoffIssueBody({
        project,
        projectSlug,
        sourceAgent: this.agentRole,
        toAgent: target,
        issueNumber,
        doneSoFar,
        nextAction,
        repo: workRepository(project, projectSlug, textOrEmpty(issue.body)),
        boundary: BOUNDARY_SENTENCE,
      });
      const handoffTitle = `[Handoff]: ${pyHead(pyStrip(title) || textOrEmpty(issue.title) || "next step", 120)}`;
      const check = validateIssue(handoffTitle, body);
      if (!check.ok) {
        return err("invalid_input", "The handoff text did not pass the coordination field check.", {
          problems: [...check.problems],
        });
      }
      const labels = ["type:handoff", "status:ready", `agent:${target}`];
      for (const name of labelNames(issue.labels || [])) {
        if (name.startsWith("project:") || name.startsWith("priority:")) {
          labels.push(name);
        }
      }
      // The new issue, a re-read, the note, a re-read, then the labels.
      this.reserve(github, 1 + 1 + 1 + 1 + statusCalls(issue), "handoff_task");
      const created = await github.createIssue({ title: handoffTitle, body, labels });
      const number = Number(created.number);
      await this.guardMutation(github, issueNumber);
      const posted = await github.createComment(
        issueNumber,
        handoffNote({ agent: this.agentRole, toAgent: target, handoffNumber: number }),
      );
      const current = await this.guardMutation(github, issueNumber);
      await this.setStatus(github, issueNumber, current, "status:blocked", this.agentRole, true);
      return ok({
        issue_number: issueNumber,
        handoff_issue: number,
        to_agent: target,
        comment_id: pyStr(posted.id),
        identity: this.identity(),
      });
    });
  }

  async requestReview(pullNumber: number): Promise<Envelope> {
    return this.mutation(async (github) => {
      const invalid = this.checkNumber(pullNumber);
      if (invalid) return invalid;
      const pull = await github.getPull(pullNumber);
      const comments = await github.listComments(pullNumber);
      const headObject = pull.head;
      const head =
        typeof headObject === "object" && headObject !== null && !Array.isArray(headObject)
          ? textOrEmpty((headObject as Json).sha)
          : "";
      if (!SHA_RE.test(head)) {
        return err("invalid_input", "The pull request has no head SHA to review.");
      }
      const pullState = (textOrEmpty(pull.state) || "open").toLowerCase();
      if (pullState === "closed" || pull.merged === true) {
        return err("not_reviewable", "This pull request is closed. request_review does not post.", {
          state: pullState,
          merged: pull.merged === true,
        });
      }
      const refusal = reviewBlock(comments, head);
      if (refusal) return refusal;
      this.reserve(github, 1, "request_review");
      const posted = await github.createComment(pullNumber, REVIEW_COMMENT);
      const { rounds } = completedReviews(comments);
      return ok({
        pull_number: pullNumber,
        comment_id: pyStr(posted.id),
        body: REVIEW_COMMENT,
        head_sha: head,
        completed_rounds: rounds.length,
        round_limit: MAX_REVIEW_ROUNDS,
        identity: this.identity(),
      });
    });
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

  // ---- write internals -----------------------------------------------------

  private get repo(): string {
    return this.config.repo ?? DEFAULT_REPO;
  }

  private get agentRole(): string {
    return this.config.agent!;
  }

  /** The readiness checks every tool runs first, then the call itself. */
  private async mutation(run: (github: GitHubWriter) => Promise<Envelope>): Promise<Envelope> {
    const blocked = this.ready();
    if (blocked) return blocked;
    return this.guarded(run);
  }

  /** Refuses before the first write when the rest of the call cannot fit in this request. */
  private reserve(github: GitHubWriter, calls: number, tool: string): void {
    const needed = calls + MINT_SLACK;
    if (github.budget.remaining < needed) {
      throw stop(
        err(
          "budget_exhausted",
          `${tool} needs more outbound GitHub calls than this request has left. Nothing was posted. ` +
            "Ask again in a new request.",
          { needed, remaining: github.budget.remaining },
        ),
      );
    }
  }

  private checkHours(value: unknown): number {
    if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 24) {
      throw stop(err("invalid_input", "lease_hours must be an integer from 1 to 24."));
    }
    return value;
  }

  private expiry(hours: number): number {
    let span = hours * 3_600_000;
    // One second under 24h so a comment timestamp slightly before this
    // clock still satisfies the 24-hour lease cap.
    if (span >= MAX_LEASE_MS) {
      span = MAX_LEASE_MS - 1000;
    }
    return this.config.now() + span;
  }

  private screen(value: string, field: string): Envelope | null {
    if (pyLen(value) > TEXT_LIMIT) {
      return err("invalid_input", `${field} is longer than ${TEXT_LIMIT} characters.`);
    }
    if (looksLikeSecret(value)) {
      return err("secret_rejected", `${field} looks like a credential. It was not posted.`);
    }
    if (decisionVerdict(value)) {
      return err("forbidden", "This server does not record or apply owner decisions.");
    }
    return null;
  }

  private checkRequiredText(value: unknown, field: string): void {
    if (typeof value !== "string" || !pyStrip(value) || pyStrip(value) === "_No response_") {
      throw stop(err("invalid_input", `${field} is empty.`));
    }
    const problem = this.screen(value, field);
    if (problem) throw stop(problem);
  }

  private checkOptionalText(value: unknown, field: string): void {
    if (value === null || value === undefined) {
      return;
    }
    if (typeof value !== "string") {
      throw stop(err("invalid_input", `${field} must be a string.`));
    }
    if (!pyStrip(value)) {
      return;
    }
    const problem = this.screen(value, field);
    if (problem) throw stop(problem);
  }

  /** The issue and its whole thread, plus how many pages the thread took. */
  private async loadPair(
    github: GitHubWriter,
    issueNumber: number,
  ): Promise<{ issue: Json; comments: Json[]; pages: number }> {
    const invalid = this.checkNumber(issueNumber);
    if (invalid) throw stop(invalid);
    const issue = await github.getIssue(issueNumber);
    const comments = await github.listComments(issueNumber);
    return { issue, comments, pages: github.lastListPages };
  }

  private async holder(
    github: GitHubWriter,
    issueNumber: number,
  ): Promise<{ issue: Json; state: LeaseState; pages: number }> {
    const { issue, comments, pages } = await this.loadPair(github, issueNumber);
    if (needsOwner(issue)) {
      throw stop(err("not_claimable", "This issue is waiting on the owner."));
    }
    const state = assessLease(comments, this.config.now());
    if (state.status === "expired") {
      throw stop(
        err(
          "lease_expired",
          "The lease has expired. claim_task starts a new one. This call did not post.",
          { lease: leaseView(state) },
        ),
      );
    }
    if (state.status !== "active" || state.agent !== this.config.agent) {
      throw stop(this.notHolding(state));
    }
    const refusal = this.holderStateRefusal(issue);
    if (refusal) throw stop(refusal);
    return { issue, state, pages };
  }

  private holderStateRefusal(issue: Json): Envelope | null {
    if ((textOrEmpty(issue.state) || "open").toLowerCase() === "closed") {
      return err("not_claimable", "This issue is closed. The call did not post.");
    }
    const labels = labelNames(issue.labels || []);
    if (labels.includes("status:done")) {
      return err("not_claimable", "This issue is already done. The call did not post.");
    }
    if (labels.includes("status:blocked")) {
      return err(
        "not_claimable",
        "This issue is blocked. The call did not post. Move it back to status:ready after the unblocker happens.",
      );
    }
    if (needsOwner(issue)) {
      return err("not_claimable", "This issue is waiting on the owner. The call did not post.");
    }
    return null;
  }

  /** Read the issue again immediately before a write. */
  private async guardMutation(github: GitHubWriter, number: number): Promise<Json> {
    const issue = await github.getIssue(number);
    const refusal = this.holderStateRefusal(issue);
    if (refusal) throw stop(refusal);
    return issue;
  }

  private claimRefusal(issue: Json, lease: LeaseState): Envelope | null {
    if (isPullRequest(issue)) {
      return err("invalid_input", "claim_task applies to issues, not pull requests.");
    }
    if ((textOrEmpty(issue.state) || "open").toLowerCase() === "closed") {
      return err("not_claimable", "This issue is closed. claim_task does not post.");
    }
    const labels = labelNames(issue.labels || []);
    const kind = kindFromTitle(textOrEmpty(issue.title));
    const isTask = labels.includes("type:task") && kind === "task";
    const isHandoff = labels.includes("type:handoff") && kind === "handoff";
    if (!isTask && !isHandoff) {
      return err(
        "not_claimable",
        "claim_task only claims open type:task issues, or a handoff for the role named in To.",
      );
    }
    if (needsOwner(issue)) {
      return err("not_claimable", "This issue is waiting on the owner. claim_task does not override that.");
    }
    if (labels.includes("status:done")) {
      return err("not_claimable", "This issue is already done.");
    }
    if (labels.includes("status:blocked")) {
      return err(
        "not_claimable",
        "This issue is blocked. claim_task does not clear that. Move it back to status:ready after the unblocker happens.",
      );
    }
    if (labels.includes("status:needs-review")) {
      return err("not_claimable", "This issue is waiting on review. claim_task does not clear that.");
    }
    const ready = labels.includes("status:ready");
    const held = labels.includes("status:claimed") || labels.includes("status:in-progress");
    const expiredHold = held && lease.status === "expired";
    const liveHold = held && lease.status === "active";
    if (!ready && !expiredHold && !liveHold) {
      return err(
        "not_claimable",
        "claim_task only claims status:ready work, or claimed or in-progress work whose lease has expired.",
      );
    }
    const role = expectedRole(issue);
    if (role && role !== "any" && role !== this.config.agent) {
      return err("wrong_role", `This issue is for ${role}.`, { expected_role: role, agent: this.config.agent });
    }
    return null;
  }

  private async winningClaim(
    github: GitHubWriter,
    issueNumber: number,
    issue: Json,
    hours: number,
  ): Promise<Envelope> {
    const title = textOrEmpty(issue.title) || `#${issueNumber}`;
    const current = this.config.now();
    const body = claimComment({
      agent: this.agentRole,
      claimedAt: stamp(current),
      expires: stamp(this.expiry(hours)),
      task: title,
    });
    const posted = await github.createComment(issueNumber, body);
    const fresh = await this.assess(github, issueNumber);
    if (fresh.status !== "active" || fresh.agent !== this.config.agent) {
      return err("lease_conflict", "The claim did not become the live lease.", {
        holder: fresh.agent,
        lease_id: fresh.leaseId,
        lease: leaseView(fresh),
      });
    }
    await this.setStatus(github, issueNumber, issue, "status:claimed", this.agentRole, true);
    return ok({
      issue_number: issueNumber,
      held: true,
      already_held: false,
      comment_id: pyStr(posted.id),
      lease: leaseView(fresh),
      identity: this.identity(),
    });
  }

  private async losingClaim(
    github: GitHubWriter,
    issueNumber: number,
    issue: Json,
    hours: number,
    holder: string | null,
  ): Promise<Envelope> {
    const title = textOrEmpty(issue.title) || `#${issueNumber}`;
    const current = this.config.now();
    const body = claimComment({
      agent: this.agentRole,
      claimedAt: stamp(current),
      expires: stamp(this.expiry(hours)),
      task: title,
    });
    const posted = await github.createComment(issueNumber, body);
    const comments = await github.listComments(issueNumber);
    const state = assessLease(comments, this.config.now());
    if (state.status === "active" && state.agent === this.config.agent) {
      await this.setStatus(github, issueNumber, issue, "status:claimed", this.agentRole, true);
      return ok({
        issue_number: issueNumber,
        held: true,
        already_held: false,
        comment_id: pyStr(posted.id),
        lease: leaseView(state),
        identity: this.identity(),
      });
    }
    if (conflictCommentNeeded(comments, state)) {
      await github.createComment(issueNumber, conflictComment(this.agentRole, leaseConflictComment(state)));
    }
    return err(
      "lease_conflict",
      `${holder || state.agent} holds the live lease. This claim was recorded and did not replace it.`,
      {
        holder: state.agent,
        lease_id: state.leaseId,
        expires: state.expires !== null ? stamp(state.expires) : null,
        marker: leaseConflictMarker(state),
        attempt_comment_id: pyStr(posted.id),
      },
    );
  }

  private async setStatus(
    github: GitHubWriter,
    number: number,
    issue: Json,
    status: string,
    agent: string | null,
    dropExpired: boolean,
  ): Promise<void> {
    const current = labelNames(issue.labels || []);
    const remove: string[] = [];
    const agentLabel = agent ? `agent:${agent}` : null;
    for (const name of current) {
      if (STATUS_LABELS.includes(name) && name !== status) {
        remove.push(name);
      } else if (dropExpired && name === "lease:expired") {
        remove.push(name);
      } else if (agentLabel && name.startsWith("agent:") && name !== agentLabel) {
        remove.push(name);
      }
    }
    const add: string[] = [];
    if (!current.includes(status)) {
      add.push(status);
    }
    if (agentLabel && !current.includes(agentLabel)) {
      add.push(agentLabel);
    }
    for (const name of remove) {
      await github.removeLabel(number, name);
    }
    await github.addLabels(number, add);
  }

  private async assess(github: GitHubWriter, number: number): Promise<LeaseState> {
    const comments = await github.listComments(number);
    return assessLease(comments, this.config.now());
  }

  private notHolding(state: LeaseState): Envelope {
    if (state.status === "expired") {
      return err("lease_expired", "The lease has expired.", { lease: leaseView(state) });
    }
    if (state.status === "active") {
      return err("lease_not_holder", `${state.agent} holds the lease.`, {
        holder: state.agent,
        lease_id: state.leaseId,
        lease: leaseView(state),
      });
    }
    return err("lease_not_holder", "This agent does not hold a live lease.", { lease: leaseView(state) });
  }

  // ---- the done gate -------------------------------------------------------

  private async doneRefusal(github: GitHubWriter, issue: Json, comments: readonly Json[]): Promise<Envelope | null> {
    if (needsOwner(issue)) {
      return err("not_claimable", "This issue is waiting on the owner. release_task will not set status:done.");
    }
    const number = Number(issue.number);
    if (!(await this.hasCompletionEvidence(github, number, comments))) {
      return err(
        "not_done",
        "status:done needs an evidence comment or an evidence issue that links this task. Nothing was posted.",
      );
    }
    const requirement = pullRequirement(issue, comments, this.repo);
    if (requirement === "waived") {
      return null;
    }
    if (requirement === "outside") {
      return err(
        "not_done",
        "A linked pull request is outside this coordination repository. " +
          "release_task cannot verify that merge and did not set status:done.",
      );
    }
    if (requirement !== "required") {
      return err(
        "not_done",
        "status:done needs a linked pull request in this repository, or text that this task needs no pull request. " +
          "Nothing was posted.",
      );
    }
    return this.pullsAreComplete(github, issue, comments);
  }

  private async hasCompletionEvidence(
    github: GitHubWriter,
    number: number,
    comments: readonly Json[],
  ): Promise<boolean> {
    for (const comment of comments) {
      if (classifyComment(textOrEmpty(comment.body)) === "evidence") {
        return true;
      }
    }
    const listing = await github.listIssues("all", MAX_LIST_PAGES);
    if (listing.truncated) {
      // Reading part of the list could miss the evidence issue. Python fails the same way.
      throw new GitHubError(502, "GitHub HTTP 502: stopped after 10 pages");
    }
    for (const item of listing.items) {
      if (isPullRequest(item)) {
        continue;
      }
      if (isEvidenceIssueFor(item, number, this.repo)) {
        return true;
      }
    }
    return false;
  }

  private async pullsAreComplete(github: GitHubWriter, issue: Json, comments: readonly Json[]): Promise<Envelope | null> {
    const numbers = linkedPullNumbers(issue, comments);
    if (numbers.length === 0) {
      return err(
        "not_done",
        "A pull request is required, but no pull request URL or number is linked. status:done was not set.",
      );
    }
    for (const pullNumber of numbers) {
      const pull = await github.getPull(pullNumber);
      const pullComments = await github.listComments(pullNumber);
      const reactions = await github.listReactions(pullNumber);
      if (pull.merged !== true) {
        return err("not_done", "The linked pull request is not merged. status:done was not set.", {
          pull_number: pullNumber,
        });
      }
      const headObject = pull.head;
      const head =
        typeof headObject === "object" && headObject !== null && !Array.isArray(headObject)
          ? textOrEmpty((headObject as Json).sha)
          : "";
      if (!reviewPassed(pullComments, reactions, head)) {
        return err(
          "not_done",
          "The linked pull request has no passing Codex review of its head. status:done was not set.",
          { pull_number: pullNumber, head_sha: head },
        );
      }
    }
    return null;
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
  private async guarded(run: (github: GitHubWriter) => Promise<Envelope>): Promise<Envelope> {
    try {
      return await run(this.config.github!);
    } catch (error) {
      if (error instanceof Stop) {
        return error.envelope;
      }
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
    return err("internal_error", "The coordination call failed unexpectedly.");
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
