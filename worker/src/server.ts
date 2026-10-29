import { McpServer } from "@modelcontextprotocol/server";
import { z } from "zod";
import type { CoordinationService, Envelope } from "./service";

export const SERVER_NAME = "egregore";
export const SERVER_VERSION = "0.1.0";

const INSTRUCTIONS =
  "Egregore coordination tools for GitHub issues in AStrasler/Egeregore. " +
  "This server is read-only: it lists tasks, reads one task and its lease, reads a task's comment history, " +
  "lists owner-decision issues, and lists pinned specialist catalog entries. " +
  "It does not claim, comment, label, merge, deploy, change settings, or record Decision: approve or Decision: reject. " +
  "A lease is a comment convention, not a lock. A recorded Decision comment is not a verified approval.";

const READ_ONLY = { readOnlyHint: true, destructiveHint: false, openWorldHint: true } as const;

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
 * Build a fresh MCP server. createMcpHandler calls this once per request, so
 * nothing here may hold state between requests. `service` is built per request
 * by the caller and carries that request's GitHub client and subrequest budget.
 *
 * `ping` is the scaffold health check. It reads no secret and calls no network.
 */
export function createServer(service: CoordinationService): McpServer {
  const server = new McpServer(
    { name: SERVER_NAME, version: SERVER_VERSION },
    { instructions: INSTRUCTIONS },
  );

  server.registerTool(
    "ping",
    {
      description: "Health check. Returns \"pong\", plus the message if one is given.",
      inputSchema: z.object({ message: z.string().max(64).optional() }),
    },
    async ({ message }) => ({
      content: [{ type: "text", text: message ? `pong: ${message}` : "pong" }],
    }),
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
    async ({ state, kind, status, project, role, limit }) =>
      respond(
        await service.getTasks({
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
    async ({ issue_number }) => respond(await service.getTask(issue_number)),
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
    async ({ state, limit }) => respond(await service.getOwnerDecisions({ state, limit })),
  );

  server.registerTool(
    "get_task_history",
    {
      description: "Read the comment history of one issue, with secrets redacted.",
      inputSchema: z.object({ issue_number: z.number().int() }),
      outputSchema: envelopeSchema,
      annotations: READ_ONLY,
    },
    async ({ issue_number }) => respond(await service.getTaskHistory(issue_number)),
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
    async (args) => respond(service.getSpecialistCatalog(args)),
  );

  return server;
}
