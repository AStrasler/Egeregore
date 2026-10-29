import { createMcpHandler } from "agents/mcp/server";
import { createServer } from "./server";

// Stateless: the handler builds a fresh server per request. No Durable Object,
// no session, no binding.
const handler = createMcpHandler(createServer, { route: "/mcp" });

export default {
  async fetch(request, env, ctx): Promise<Response> {
    return handler(request, env, ctx);
  },
} satisfies ExportedHandler;
