import { McpServer } from "@modelcontextprotocol/server";
import { z } from "zod";

export const SERVER_NAME = "egregore";
export const SERVER_VERSION = "0.0.0";

/**
 * Build a fresh MCP server. createMcpHandler calls this once per request, so
 * nothing here may hold state between requests.
 *
 * Scaffold: `ping` is the only tool. It reads no secret, calls no network, and
 * touches no GitHub data. The coordination tools arrive in later pull requests.
 */
export function createServer(): McpServer {
  const server = new McpServer({ name: SERVER_NAME, version: SERVER_VERSION });

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

  return server;
}
