import { createMcpHandler } from "agents/mcp/server";
import {
  DEFAULT_REPO,
  GITHUB_API,
  GitHubClient,
  SubrequestBudget,
  type FetchLike,
} from "./github";
import { GitHubAppAuth } from "./github-app";
import { CoordinationService } from "./service";
import { createServer } from "./server";

/** Plain variables and secrets. Every field is optional because none is set yet. */
export interface Env {
  EGREGORE_APP_ID?: string;
  EGREGORE_APP_INSTALLATION_ID?: string;
  EGREGORE_APP_PRIVATE_KEY?: string;
  EGREGORE_REPO?: string;
}

export interface Deps {
  fetch: FetchLike;
  now: () => number;
  /**
   * Who is calling, as an Agent role. There is no authentication layer yet, so
   * the default returns null and every coordination tool answers
   * `unauthenticated`. The OAuth pull request replaces this.
   */
  resolveAgent: (request: Request) => string | null;
  /** The GitHub API origin. Fixed in production. Tests point it at a fake. */
  api: string;
}

const REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
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
    resolveAgent: () => null,
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
    if (!REPO_RE.test(repo)) problems.push("EGREGORE_REPO");
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
    return new CoordinationService({ agent, github, configProblems: problems, now: deps.now });
  }

  return {
    async fetch(request, env, ctx) {
      // Stateless: a fresh server, service, GitHub client and subrequest budget
      // for every request. No Durable Object, no session, no binding.
      const service = buildService(env ?? {}, deps.resolveAgent(request));
      const handler = createMcpHandler(() => createServer(service), { route: "/mcp" });
      return handler(request, env, ctx);
    },
  };
}

export default createWorker();
