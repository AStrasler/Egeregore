/**
 * Codex review bookkeeping for request_review and the done gate: a port of
 * completed_reviews, review_block and their helpers in
 * mcp/egregore_mcp/service.py. A summary comment and a receipt for the same
 * commit are one round. Only comments from the Codex connector bot count.
 */
import { author, type Json } from "./coordination";
import { REVIEW_COMMENT } from "./comments";
import { PY_WS, pyStr, pyStrip, pyTruthy, textOrEmpty } from "./pycompat";
import { failure, type Envelope } from "./specialists";

export const MAX_REVIEW_ROUNDS = 3;
export const CODEX_BOT = "chatgpt-codex-connector";
const STATE_PREFIX = "codex-review-state:v1 ";
const RECEIPT_RE = new RegExp(`Reviewed commit:\\*\\*[${PY_WS}]*\`([0-9a-fA-F]{7,40})\``, "u");
export const SHA_RE = /^[0-9a-fA-F]{7,40}$/;

export interface Round {
  sha: string;
  unresolved: boolean | null;
  commentId: string;
  source: "summary" | "receipt";
}

export function sameCommit(left: string, right: string): boolean {
  const [shorter, longer] = [left.toLowerCase(), right.toLowerCase()].sort((a, b) => a.length - b.length);
  return shorter !== "" && longer.startsWith(shorter);
}

/**
 * The JSON object after "codex-review-state:v1 ". Python reads one JSON value
 * from the first "{" and ignores what follows. This finds the matching "}" the
 * same way, then parses that slice.
 */
export function reviewState(body: string): Json | null {
  const index = body.indexOf(STATE_PREFIX);
  if (index === -1) {
    return null;
  }
  const start = body.indexOf("{", index);
  if (start === -1) {
    return null;
  }
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let position = start; position < body.length; position += 1) {
    const char = body[position];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }
    if (char === '"') {
      inString = true;
    } else if (char === "{") {
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        try {
          const value: unknown = JSON.parse(body.slice(start, position + 1));
          return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Json) : null;
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}

function mergedUnresolved(existing: Round, item: Round): boolean | null {
  // The summary flag wins over a receipt for the same commit, in either order.
  if (existing.source === "summary" && item.source !== "summary") {
    return existing.unresolved;
  }
  if (item.source === "summary" && existing.source !== "summary") {
    return item.unresolved;
  }
  if (item.unresolved !== null) {
    return item.unresolved;
  }
  return existing.unresolved;
}

function mergeRounds(rounds: readonly Round[]): Round[] {
  const merged: Round[] = [];
  for (const item of rounds) {
    const index = merged.findIndex((existing) => sameCommit(existing.sha, item.sha));
    if (index === -1) {
      merged.push({ ...item });
      continue;
    }
    const existing = merged[index];
    merged[index] = {
      sha: item.sha.length >= existing.sha.length ? item.sha : existing.sha,
      unresolved: mergedUnresolved(existing, item),
      commentId: existing.commentId,
      source: existing.source === "summary" || item.source === "summary" ? "summary" : item.source,
    };
  }
  return merged;
}

/** Distinct completed Codex reviews, oldest first, plus a pending status. */
export function completedReviews(comments: readonly Json[]): { rounds: Round[]; pending: string | null } {
  const rounds: Round[] = [];
  let pending: string | null = null;
  let lastRequest = "";
  let lastResult = "";
  for (const comment of comments) {
    const body = textOrEmpty(comment.body);
    // Summary comments are edited in place, so updated_at is the completion time.
    // A review request uses created_at.
    const resultAt = textOrEmpty(comment.updated_at) || textOrEmpty(comment.created_at);
    const requestAt = textOrEmpty(comment.created_at);
    // A copied payload must not count as Codex. Only the connector bot can
    // close a round or clear findings.
    const fromCodex = author(comment).toLowerCase() === `${CODEX_BOT}[bot]`;
    const state = fromCodex ? reviewState(body) : null;
    if (state !== null) {
      const codeReview = state.code_review;
      if (typeof codeReview === "object" && codeReview !== null && !Array.isArray(codeReview)) {
        const review = codeReview as Json;
        const status = review.status;
        const sha = textOrEmpty(review.commit_sha);
        if (status === "completed" && sha) {
          rounds.push({
            sha,
            unresolved: pyTruthy(review.has_unresolved_findings),
            commentId: "id" in comment ? pyStr(comment.id) : "",
            source: "summary",
          });
          if (resultAt >= lastResult) {
            lastResult = resultAt;
          }
        } else if (status !== null && status !== undefined && status !== "" && status !== "completed") {
          pending = pyStr(status);
        }
      }
    }
    if (fromCodex && body.includes("codex-review-receipt")) {
      const found = RECEIPT_RE.exec(body);
      if (found) {
        rounds.push({
          sha: found[1],
          unresolved: body.includes("Didn't find any major issues") ? false : null,
          commentId: "id" in comment ? pyStr(comment.id) : "",
          source: "receipt",
        });
      }
      if (resultAt >= lastResult) {
        lastResult = resultAt;
      }
    }
    if (pyStrip(body) === REVIEW_COMMENT && requestAt >= lastRequest) {
      lastRequest = requestAt;
    }
  }
  if (pending === null && lastRequest && lastRequest > lastResult) {
    pending = "requested";
  }
  return { rounds: mergeRounds(rounds), pending };
}

/** null when a new review may be requested, else the refusal envelope. */
export function reviewBlock(comments: readonly Json[], head: string): Envelope | null {
  const { rounds, pending } = completedReviews(comments);
  const base = { completed_rounds: rounds.length, round_limit: MAX_REVIEW_ROUNDS, head_sha: head };
  if (pending) {
    return failure(
      "review_pending",
      "A Codex review is still running for this pull request. Wait for the receipt before asking again.",
      { status: pending, ...base },
    );
  }
  const matched = rounds.filter((item) => sameCommit(item.sha, head));
  const last = matched.length > 0 ? matched[matched.length - 1] : null;
  if (last !== null && last.unresolved === false) {
    return failure("review_current", "The review receipt already names this head and reports no unresolved findings.", {
      reviewed_sha: last.sha,
      ...base,
    });
  }
  if (last !== null && last.unresolved === true) {
    return failure(
      "review_same_head",
      "Codex already reviewed this head and left findings. Push a new commit before requesting another review.",
      { reviewed_sha: last.sha, ...base },
    );
  }
  if (last !== null) {
    return failure(
      "review_duplicate",
      "A Codex review already covers this head commit. Push a new commit before requesting another review.",
      { reviewed_sha: last.sha, ...base },
    );
  }
  if (rounds.length >= MAX_REVIEW_ROUNDS) {
    return failure(
      "review_limit",
      "Three Codex reviews are the limit. Open one owner-decision issue instead of a fourth request.",
      base,
    );
  }
  return null;
}

/**
 * A pull request's head passes when the newest round for that commit came from
 * the summary comment, left nothing unresolved, and the Codex bot reacted +1.
 */
export function reviewPassed(comments: readonly Json[], reactions: readonly Json[], head: string): boolean {
  if (!head) {
    return false;
  }
  const { rounds } = completedReviews(comments);
  const matched = rounds.filter((item) => sameCommit(item.sha, head));
  if (matched.length === 0) {
    return false;
  }
  const latest = matched[matched.length - 1];
  if (latest.source !== "summary" || latest.unresolved !== false) {
    return false;
  }
  return reactions.some(
    (reaction) => textOrEmpty(reaction.content) === "+1" && author(reaction).toLowerCase().includes(CODEX_BOT),
  );
}
