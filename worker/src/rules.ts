/**
 * The text rules behind the done gate: a port of the pull-request and
 * evidence helpers in mcp/egregore_mcp/service.py. They are pure, so the same
 * cases run against both implementations.
 */
import { firstLine, kindFromTitle, labelNames, sectionMap, validateIssue, type Json } from "./coordination";
import { WORD_END, WORD_START, textOrEmpty } from "./pycompat";

const PULL_URL_RE = /https:\/\/github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\/pull\/([0-9]+)/giu;
const NO_PULL_RE = new RegExp(
  `${WORD_START}(without a pull request|no pull request|no pr|documentation only|docs only|research only)${WORD_END}`,
  "giu",
);
const WANTS_PULL_RE = new RegExp(`${WORD_START}pull request${WORD_END}`, "iu");
const ISSUE_REF_RE = new RegExp(`(?<![\\p{L}\\p{N}_/])#([0-9]+)${WORD_END}`, "gu");

export interface PullLink {
  owner: string;
  name: string;
  number: number;
}

export function pullLinks(text: string): PullLink[] {
  return [...text.matchAll(PULL_URL_RE)].map((match) => ({
    owner: match[1],
    name: match[2],
    number: Number(match[3]),
  }));
}

const escapeRegExp = (value: string): string => value.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

/** True when `body` points at issue `number` of `repo` by #n or by a qualified reference. */
export function mentionsIssue(body: string, number: number, repo: string): boolean {
  for (const match of body.matchAll(ISSUE_REF_RE)) {
    if (Number(match[1]) === number) {
      return true;
    }
  }
  const slash = repo.indexOf("/");
  const owner = slash === -1 ? repo : repo.slice(0, slash);
  const name = slash === -1 ? "" : repo.slice(slash + 1);
  if (!owner || !name) {
    return false;
  }
  const where = `${escapeRegExp(owner)}/${escapeRegExp(name)}`;
  const qualified = new RegExp(
    `(?:https://github\\.com/${where}/issues/${number}${WORD_END}|` +
      `(?<![\\p{L}\\p{N}_./])${where}/issues/${number}${WORD_END}|` +
      `(?<![\\p{L}\\p{N}_./])${where}#${number}${WORD_END})`,
    "iu",
  );
  return qualified.test(body);
}

export type PullRequirement = "waived" | "outside" | "required" | "unknown";

export function pullRequirement(issue: Json, comments: readonly Json[], repo: string): PullRequirement {
  const body = textOrEmpty(issue.body);
  const text = [body, ...comments.map((comment) => textOrEmpty(comment.body))].join("\n");
  const links = pullLinks(text);
  if (links.length > 0) {
    const wanted = repo.toLowerCase();
    if (links.some((link) => `${link.owner}/${link.name}`.toLowerCase() !== wanted)) {
      return "outside";
    }
    return "required";
  }
  const sections = sectionMap(body);
  const described = ["Outcome", "Context", "Out of scope", "Done so far", "Next action"]
    .map((name) => sections.get(name) ?? "")
    .join("\n");
  // Waiver phrases are removed before this search, so "no pull request" is not a
  // requirement. An explicit request still wins over a waiver, including "no merge".
  if (WANTS_PULL_RE.test(described.replace(NO_PULL_RE, " "))) {
    return "required";
  }
  if (new RegExp(NO_PULL_RE.source, "iu").test(described)) {
    return "waived";
  }
  const project = firstLine(sections.get("Project")).toLowerCase();
  if (project === "other" && !WANTS_PULL_RE.test(described)) {
    return "waived";
  }
  if (WANTS_PULL_RE.test(described)) {
    return "required";
  }
  return "unknown";
}

/** Distinct pull numbers named in the task text, in the order first seen. */
export function linkedPullNumbers(issue: Json, comments: readonly Json[]): number[] {
  const text = [textOrEmpty(issue.body), ...comments.map((comment) => textOrEmpty(comment.body))].join("\n");
  const numbers: number[] = [];
  for (const link of pullLinks(text)) {
    if (!numbers.includes(link.number)) {
      numbers.push(link.number);
    }
  }
  return numbers;
}

/** An issue (not the task itself) that is an evidence issue and names the task. */
export function isEvidenceIssueFor(item: Json, number: number, repo: string): boolean {
  const itemNumber = Number(item.number || 0);
  if (!Number.isFinite(itemNumber) || itemNumber === number) {
    return false;
  }
  const labels = labelNames(item.labels || []);
  const kind = kindFromTitle(textOrEmpty(item.title));
  if (!labels.includes("type:evidence") && kind !== "evidence") {
    return false;
  }
  return mentionsIssue(textOrEmpty(item.body), number, repo);
}

export function needsOwner(issue: Json): boolean {
  const labels = labelNames(issue.labels || []);
  if (labels.includes("status:needs-owner")) {
    return true;
  }
  const title = textOrEmpty(issue.title);
  if (kindFromTitle(title) === "decision") {
    return true;
  }
  // Hygiene may not have copied Approval needed: yes onto the label yet.
  return validateIssue(title, textOrEmpty(issue.body)).needsOwner;
}
