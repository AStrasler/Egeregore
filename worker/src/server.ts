import { McpServer } from "@modelcontextprotocol/server";
import { z } from "zod";
import type { CoordinationService, Envelope } from "./service";

export const SERVER_NAME = "egregore";
export const SERVER_VERSION = "0.1.0";

const INSTRUCTIONS =
  "Egregore coordination tools for GitHub issues in AStrasler/Egeregore. " +
  "It lists tasks, reads one task and its lease, reads a task's comment history, " +
  "lists owner-decision issues, and lists pinned specialist catalog entries. " +
  "It also claims and renews a lease, posts progress, releases a task, opens a handoff issue, and asks Codex to review a pull request. " +
  "Those writes only comment, label, and open handoff issues. " +
  "It never merges, deploys, changes settings, or records Decision: approve or Decision: reject. " +
  "A lease is a comment convention, not a lock. A recorded Decision comment is not a verified approval.";

const READ_ONLY = { readOnlyHint: true, destructiveHint: false, openWorldHint: true } as const;
const WRITE = { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true } as const;

// The same envelope the Python server returns: { ok, result, error }.
const envelopeSchema = z.object({
  ok: z.boolean(),
  result: z.record(z.string(), z.unknown()).nullable().default(null),
  error: z
    .object({
      code: z.string(),
      message: z.string(),
      details: z.record(z.string(), z.unknown()).default({}),
    })
    .nullable()
    .default(null),
});

/** Structured result plus the same indent-2 JSON text the Python server sends. */
function respond(envelope: Envelope) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(envelope, null, 2) }],
    structuredContent: envelope,
  };
}

/**
 * Decides whether this request's caller may use a tool. It returns the refusal
 * envelope, or null to let the call through. Required, so a server cannot be
 * built without an access decision.
 */
export type Gate = (tool: string) => Envelope | null;

/**
 * Build a fresh MCP server. createMcpHandler calls this once per request, so
 * nothing here may hold state between requests. `service` is built per request
 * by the caller and carries that request's GitHub client and subrequest budget.
 *
 * `ping` is the scaffold health check. It reads no secret and calls no network.
 */
export function createServer(service: CoordinationService, gate: Gate): McpServer {
  const server = new McpServer(
    { name: SERVER_NAME, version: SERVER_VERSION },
    { instructions: INSTRUCTIONS },
  );

  // Every handler goes through `guarded`, which asks the gate first. A tool
  // that skipped it would be open, so no handler is registered without it.
  const guarded =
    <A>(tool: string, run: (args: A) => Promise<Envelope> | Envelope) =>
    async (args: A) =>
      respond(gate(tool) ?? (await run(args)));

  server.registerTool(
    "ping",
    {
      description: "Health check. Returns \"pong\", plus the message if one is given.",
      inputSchema: z.object({ message: z.string().max(64).optional() }),
    },
    async ({ message }) => {
      const refused = gate("ping");
      if (refused) return respond(refused);
      return { content: [{ type: "text" as const, text: message ? `pong: ${message}` : "pong" }] };
    },
  );

  server.registerTool(
    "get_tasks",
    {
      description: "List coordination issues and the lease each one currently holds.",
      inputSchema: z.object({
        state: z.string().default("open"),
        kind: z.string().default("task"),
        status: z.string().default(""),
        project: z.string().default(""),
        role: z.string().default(""),
        limit: z.number().int().default(30),
      }),
      outputSchema: envelopeSchema,
      annotations: READ_ONLY,
    },
    guarded("get_tasks", ({ state, kind, status, project, role, limit }) =>
      service.getTasks({
        state,
        kind: kind || null,
        status: status || null,
        project: project || null,
        role: role || null,
        limit,
      }),
    ),
  );

  server.registerTool(
    "get_task",
    {
      description: "Read one issue, its fields, and the lease held on it.",
      inputSchema: z.object({ issue_number: z.number().int() }),
      outputSchema: envelopeSchema,
      annotations: READ_ONLY,
    },
    guarded("get_task", ({ issue_number }) => service.getTask(issue_number)),
  );

  server.registerTool(
    "get_owner_decisions",
    {
      description:
        "List owner-decision issues and Decision comments. recorded is not a verified approval.",
      inputSchema: z.object({
        state: z.string().default("open"),
        limit: z.number().int().default(30),
      }),
      outputSchema: envelopeSchema,
      annotations: READ_ONLY,
    },
    guarded("get_owner_decisions", ({ state, limit }) => service.getOwnerDecisions({ state, limit })),
  );

  server.registerTool(
    "get_task_history",
    {
      description: "Read the comment history of one issue, with secrets redacted.",
      inputSchema: z.object({ issue_number: z.number().int() }),
      outputSchema: envelopeSchema,
      annotations: READ_ONLY,
    },
    guarded("get_task_history", ({ issue_number }) => service.getTaskHistory(issue_number)),
  );

  server.registerTool(
    "get_specialists",
    {
      description:
        "Find pinned specialist agent/skill source files. Does not run or install prompts.",
      inputSchema: z.object({
        role: z.string().default(""),
        kind: z.string().default(""),
        specialist_id: z.string().default(""),
      }),
      outputSchema: envelopeSchema,
      annotations: READ_ONLY,
    },
    guarded("get_specialists", (args) => service.getSpecialistCatalog(args)),
  );

  server.registerTool(
    "claim_task",
    {
      description: "Claim a ready issue. The earliest live lease wins. A later agent is a conflict.",
      inputSchema: z.object({ issue_number: z.number().int(), lease_hours: z.number().int().default(6) }),
      outputSchema: envelopeSchema,
      annotations: WRITE,
    },
    guarded("claim_task", ({ issue_number, lease_hours }) => service.claimTask(issue_number, lease_hours)),
  );

  server.registerTool(
    "renew_lease",
    {
      description: "Renew the caller's live lease. The original claim comment id stays.",
      inputSchema: z.object({
        issue_number: z.number().int(),
        lease_hours: z.number().int().default(6),
        note: z.string().default(""),
      }),
      outputSchema: envelopeSchema,
      annotations: WRITE,
    },
    guarded("renew_lease", ({ issue_number, lease_hours, note }) =>
      service.renewLease(issue_number, lease_hours, note),
    ),
  );

  server.registerTool(
    "release_task",
    {
      description:
        "Release the caller's lease. status:done needs evidence, and a linked pull request must be merged and reviewed. A required pull request with no link is refused.",
      inputSchema: z.object({ issue_number: z.number().int(), status: z.string(), reason: z.string() }),
      outputSchema: envelopeSchema,
      annotations: WRITE,
    },
    guarded("release_task", ({ issue_number, status, reason }) => service.releaseTask(issue_number, status, reason)),
  );

  server.registerTool(
    "post_progress",
    {
      description: "Post a progress comment. The caller must hold the live lease.",
      inputSchema: z.object({ issue_number: z.number().int(), done: z.string(), next: z.string() }),
      outputSchema: envelopeSchema,
      annotations: WRITE,
    },
    guarded("post_progress", ({ issue_number, done, next }) => service.postProgress(issue_number, done, next)),
  );

  server.registerTool(
    "handoff_task",
    {
      description:
        "Open a handoff issue for another role and release the source lease. It does not grant access.",
      inputSchema: z.object({
        issue_number: z.number().int(),
        to_agent: z.string(),
        done_so_far: z.string(),
        next_action: z.string(),
        title: z.string().default(""),
      }),
      outputSchema: envelopeSchema,
      annotations: WRITE,
    },
    guarded("handoff_task", ({ issue_number, to_agent, done_so_far, next_action, title }) =>
      service.handoffTask(issue_number, to_agent, done_so_far, next_action, title),
    ),
  );

  server.registerTool(
    "request_review",
    {
      description:
        "Post one @codex review on an open pull request. Closed pulls, a repeat of the same head, and a fourth round are refused.",
      inputSchema: z.object({ pull_number: z.number().int() }),
      outputSchema: envelopeSchema,
      annotations: WRITE,
    },
    guarded("request_review", ({ pull_number }) => service.requestReview(pull_number)),
  );

  return server;
}
