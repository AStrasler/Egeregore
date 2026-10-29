import { createMcpHandler } from "agents/mcp/server";
import {
  DEFAULT_REPO,
  GITHUB_API,
  GitHubClient,
  SubrequestBudget,
  type FetchLike,
} from "./github";
import { GitHubAppAuth } from "./github-app";
import { authenticateRequest, decide, safeRepo, type Principal } from "./auth";
import { CoordinationService, err, type Envelope } from "./service";
import type { Json } from "./coordination";
import { createServer } from "./server";

/** Plain variables and secrets. Every field is optional because none is set yet. */
export interface Env {
  EGREGORE_APP_ID?: string;
  EGREGORE_APP_INSTALLATION_ID?: string;
  EGREGORE_APP_PRIVATE_KEY?: string;
  EGREGORE_REPO?: string;
  /**
   * A secret. A JSON array of token records: the SHA-256 of each bearer token,
   * the agent it belongs to, and what it may do. See worker/README.md. Unset
   * or invalid means every tool answers `unauthenticated`.
   */
  EGREGORE_AGENT_TOKENS?: string;
}

export interface Deps {
  fetch: FetchLike;
  now: () => number;
  /**
   * Who is calling. The default checks the bearer token against the hashed
   * records in `EGREGORE_AGENT_TOKENS` and returns that token's principal, or
   * null, which makes every tool answer `unauthenticated`. Tests replace it.
   */
  authenticate: (request: Request, env: Env) => Promise<Principal | null>;
  /** The GitHub API origin. Fixed in production. Tests point it at a fake. */
  api: string;
}

const DIGITS_RE = /^[0-9]+$/;

interface AuthSlot {
  fingerprint: string;
  privateKey: string;
  fetch: FetchLike;
  auth: GitHubAppAuth;
}

/** Builds the Worker. Production uses the defaults. Tests pass fakes. */
export interface Worker {
  fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response>;
}

export function createWorker(overrides: Partial<Deps> = {}): Worker {
  const deps: Deps = {
    fetch: (url, init) => fetch(url, init),
    now: () => Date.now(),
    authenticate: (request, env) => authenticateRequest(request, env),
    api: GITHUB_API,
    ...overrides,
  };
  // One slot per isolate. The installation token and the imported signing key
  // live in it between requests. It is replaced when any setting changes.
  let slot: AuthSlot | null = null;

  function authFor(appId: string, installationId: string | null, privateKey: string, repo: string): GitHubAppAuth {
    const fingerprint = `${deps.api}|${repo}|${appId}|${installationId ?? ""}`;
    if (slot && slot.fingerprint === fingerprint && slot.privateKey === privateKey && slot.fetch === deps.fetch) {
      return slot.auth;
    }
    const auth = new GitHubAppAuth({
      api: deps.api,
      appId,
      installationId,
      privateKey,
      repo,
      now: deps.now,
      fetch: deps.fetch,
    });
    slot = { fingerprint, privateKey, fetch: deps.fetch, auth };
    return auth;
  }

  function buildService(env: Env, agent: string | null): CoordinationService {
    const problems: string[] = [];
    const repo = (env.EGREGORE_REPO ?? "").trim() || DEFAULT_REPO;
    if (safeRepo(repo) === null) problems.push("EGREGORE_REPO");
    const appId = (env.EGREGORE_APP_ID ?? "").trim();
    if (!DIGITS_RE.test(appId)) problems.push("EGREGORE_APP_ID");
    const installation = (env.EGREGORE_APP_INSTALLATION_ID ?? "").trim();
    if (installation && !DIGITS_RE.test(installation)) problems.push("EGREGORE_APP_INSTALLATION_ID");
    const privateKey = (env.EGREGORE_APP_PRIVATE_KEY ?? "").trim();
    if (!privateKey) problems.push("EGREGORE_APP_PRIVATE_KEY");

    let github: GitHubClient | null = null;
    if (problems.length === 0) {
      const budget = new SubrequestBudget();
      const auth = authFor(appId, installation || null, privateKey, repo);
      github = new GitHubClient({
        api: deps.api,
        repo,
        budget,
        fetch: deps.fetch,
        token: () => auth.token(budget),
      });
    }
    return new CoordinationService({ agent, github, configProblems: problems, now: deps.now, repo });
  }

  return {
    async fetch(request, env, ctx) {
      // Stateless: a fresh server, service, GitHub client and subrequest budget
      // for every request. No Durable Object, no session, no binding.
      const settings = env ?? {};
      const principal = await deps.authenticate(request, settings);
      const repo = (settings.EGREGORE_REPO ?? "").trim() || DEFAULT_REPO;
      // The agent comes from the token's record. Nothing in the request body
      // can name or change it.
      const service = buildService(settings, principal?.agent ?? null);
      const gate = (tool: string): Envelope | null => {
        const decision = decide(principal, tool, repo);
        return decision.allowed ? null : err(decision.code, decision.message, decision.details as Json);
      };
      const handler = createMcpHandler(() => createServer(service, gate), { route: "/mcp" });
      return handler(request, env, ctx);
    },
  };
}

export default createWorker();
