/**
 * Comment and issue text for the write tools: a port of
 * mcp/egregore_mcp/comments.py. Lease lines match OPERATIONS.md, and
 * scripts/coordination.py parses Claim-Agent, Lease-Expires and Lease-Release
 * from them. The Agent line is the role, not a GitHub login. A review request
 * is the exception: Codex starts a review only when the pull-request comment is
 * the single line `@codex review`.
 */
import { PY_WS, pyStrip, splitNewlines } from "./pycompat";

const D = "\\p{Nd}";

export const DISPLAY_NAMES: Readonly<Record<string, string>> = {
  chatgpt: "ChatGPT",
  grokbot: "Grokbot",
  cursor: "Cursor",
  codex: "Codex",
  aaron: "Aaron",
};

export const REVIEW_COMMENT = "@codex review";

// Same anchors as scripts/coordination.py. assess_lease matches these after
// stripping the line, including inside a Markdown fence.
const LEASE_CONTROL_RES: readonly RegExp[] = [
  new RegExp(`^Lease-Expires:[${PY_WS}]*${D}{4}-${D}{2}-${D}{2}T${D}{2}:${D}{2}:${D}{2}Z[${PY_WS}]*$`, "u"),
  new RegExp(`^Claim-Agent:[${PY_WS}]*[A-Za-z]+[${PY_WS}]*$`, "u"),
  new RegExp(`^Lease-Release:[${PY_WS}]*[A-Za-z]+[${PY_WS}]*$`, "u"),
];

export const ACCESS_SENTENCE =
  "This handoff does not grant repository access, data access, credentials, " +
  "or permission to merge or deploy.";

export function displayName(agent: string): string {
  const name = DISPLAY_NAMES[agent];
  if (name === undefined) {
    throw new Error(`no display name for ${agent}`);
  }
  return name;
}

export function agentPreamble(agent: string): string {
  return `Agent: ${displayName(agent)}`;
}

/** True when a free-form field contains a line assess_lease would apply. */
export function containsLeaseControl(value: string): boolean {
  return splitNewlines(value).some((raw) => {
    const line = pyStrip(raw);
    return LEASE_CONTROL_RES.some((pattern) => pattern.test(line));
  });
}

/**
 * Quote lease-control lines so assess_lease ignores them. The parser strips
 * indentation and still matches a line inside a fence. A leading `>` survives
 * that strip and breaks the `^Lease-` anchor.
 */
export function neutralizeLeaseLines(value: string): string {
  return splitNewlines(value)
    .map((raw) => {
      const line = pyStrip(raw);
      return LEASE_CONTROL_RES.some((pattern) => pattern.test(line)) ? `> ${line}` : raw;
    })
    .join("\n");
}

// Documented project repositories. coordination is this repo. WHOOP Lens is
// public at that URL. Weather Intelligence has no URL in this repo's docs.
const PROJECT_REPOS: Readonly<Record<string, string>> = {
  coordination: "https://github.com/AStrasler/Egeregore",
  "whoop-lens": "https://github.com/AStrasler/WHOOP-Lens",
};
const GITHUB_REPO_RE = /https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+/g;

/** Where the work happens. Never substitute the coordination repo for another project. */
export function workRepository(project: string, projectSlug: string, body: string): string {
  const name = pyStrip(project).toLowerCase();
  const slug = pyStrip(projectSlug).toLowerCase();
  const key = name === "other" && slug ? slug : name;
  if (Object.hasOwn(PROJECT_REPOS, key)) {
    return PROJECT_REPOS[key];
  }
  for (const match of (body || "").matchAll(GITHUB_REPO_RE)) {
    const url = match[0];
    if (url.toLowerCase().endsWith("/astrasler/egeregore")) {
      continue;
    }
    return url;
  }
  if (key === "weather-intelligence") {
    return "the Weather Intelligence repository";
  }
  const label = pyStrip(project) || "the project";
  return `the ${label} repository`;
}

/** Keep the coordination marker and name the role that posted it. */
export function conflictComment(agent: string, body: string): string {
  return `${agentPreamble(agent)}\n\n${body.replace(/^\n+/, "")}`;
}

export function claimComment(args: { agent: string; claimedAt: string; expires: string; task: string }): string {
  return [
    agentPreamble(args.agent),
    "",
    "## Claim",
    `Claim-Agent: ${args.agent}`,
    `Claimed-At: ${args.claimedAt}`,
    `Lease-Expires: ${args.expires}`,
    "Task: " + neutralizeLeaseLines(args.task),
    "Data-Boundary: coordination-only",
    "",
  ].join("\n");
}

export function renewalComment(args: { agent: string; expires: string; note: string }): string {
  const lines = [
    agentPreamble(args.agent),
    "",
    "## Lease renewal",
    `Claim-Agent: ${args.agent}`,
    `Lease-Expires: ${args.expires}`,
  ];
  if (pyStrip(args.note)) {
    lines.push("Note: " + neutralizeLeaseLines(pyStrip(args.note)));
  }
  lines.push("Data-Boundary: coordination-only", "");
  return lines.join("\n");
}

export function releaseComment(args: { agent: string; reason: string }): string {
  return [
    agentPreamble(args.agent),
    "",
    "## Lease release",
    `Claim-Agent: ${args.agent}`,
    `Lease-Release: ${args.agent}`,
    "Reason: " + neutralizeLeaseLines(pyStrip(args.reason)),
    "Data-Boundary: coordination-only",
    "",
  ].join("\n");
}

export function progressComment(args: { agent: string; done: string; nextStep: string }): string {
  return [
    agentPreamble(args.agent),
    "",
    "## Progress",
    `Claim-Agent: ${args.agent}`,
    "Done: " + neutralizeLeaseLines(pyStrip(args.done)),
    "Next: " + neutralizeLeaseLines(pyStrip(args.nextStep)),
    "Data-Boundary: coordination-only",
    "",
  ].join("\n");
}

export function handoffIssueBody(args: {
  project: string;
  projectSlug: string;
  sourceAgent: string;
  toAgent: string;
  issueNumber: number;
  doneSoFar: string;
  nextAction: string;
  repo: string;
  boundary: string;
}): string {
  const access = `${ACCESS_SENTENCE} The work stays in ${args.repo}. This issue does not add access there.`;
  const lines = ["### Project", "", args.project, ""];
  if (args.projectSlug) {
    lines.push("### Project slug", "", args.projectSlug, "");
  }
  lines.push(
    "### From",
    "",
    args.sourceAgent,
    "",
    "### To",
    "",
    args.toAgent,
    "",
    "### Related issue",
    "",
    `#${args.issueNumber}`,
    "",
    "### Done so far",
    "",
    neutralizeLeaseLines(pyStrip(args.doneSoFar)),
    "",
    "### Next action",
    "",
    neutralizeLeaseLines(pyStrip(args.nextAction)),
    "",
    "### Access",
    "",
    access,
    "",
    "### Data boundary",
    "",
    `- [x] ${args.boundary}`,
    "",
  );
  return lines.join("\n");
}

export function handoffNote(args: { agent: string; toAgent: string; handoffNumber: number }): string {
  return [
    agentPreamble(args.agent),
    "",
    "## Handoff",
    `Claim-Agent: ${args.agent}`,
    `Lease-Release: ${args.agent}`,
    `To: ${args.toAgent}`,
    `Handoff-Issue: #${args.handoffNumber}`,
    `Reason: next step belongs to ${args.toAgent}`,
    "Data-Boundary: coordination-only",
    "",
  ].join("\n");
}
