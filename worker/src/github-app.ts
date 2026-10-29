/**
 * GitHub App authentication for one installation on one repository: a port of
 * mcp/egregore_mcp/github_app.py to WebCrypto.
 *
 * The Worker signs a short-lived RS256 JWT with the app private key and
 * exchanges it at POST /app/installations/{id}/access_tokens. The token request
 * asks only for issues:write, pull_requests:read and metadata:read, and only
 * for the one repository. A token whose repository list is anything else, or
 * whose permissions are broader, is refused. The token is cached per isolate
 * until five minutes before it expires.
 *
 * Nothing here logs or returns the key, the JWT, or a token. Errors go through
 * a scrubber that replaces all three.
 */
import {
  GitHubError,
  Scrubber,
  SubrequestBudgetError,
  nextLink,
  safeError,
  sameOrigin,
  send,
  type FetchLike,
  type SubrequestBudget,
} from "./github";
import { redactText } from "./coordination";
import { tryParseTime } from "./pycompat";

export const REQUESTED_PERMISSIONS: Readonly<Record<string, string>> = {
  issues: "write",
  pull_requests: "read",
  metadata: "read",
};
const LEVELS: Readonly<Record<string, number>> = { read: 1, write: 2, admin: 3 };
const REFRESH_SKEW_MS = 5 * 60 * 1000;
const JWT_PAST_SKEW_S = 60;
const JWT_FUTURE_S = 8 * 60;

export class GitHubAppError extends Error {
  constructor(
    message: string,
    readonly status: number = 0,
  ) {
    super(message);
    this.name = "GitHubAppError";
  }
}
/** The app environment is incomplete or the private key cannot be used. */
export class GitHubAppConfigError extends GitHubAppError {}
/** The installation token can see another repository or a broader permission. */
export class GitHubAppScopeError extends GitHubAppError {}

// ---- Private key handling -------------------------------------------------

/** Returns a PEM private key block as one string, or throws a config error. */
export function normalizePem(text: string): string {
  let cleaned = text.trim();
  if (cleaned.length >= 2 && cleaned[0] === cleaned[cleaned.length - 1] && (cleaned[0] === '"' || cleaned[0] === "'")) {
    cleaned = cleaned.slice(1, -1).trim();
  }
  // Secrets pasted into a dashboard often carry literal \n instead of newlines.
  cleaned = cleaned.replaceAll("\\n", "\n");
  if (!cleaned.includes("-----BEGIN ") || !cleaned.includes("PRIVATE KEY-----")) {
    throw new GitHubAppConfigError("The GitHub App private key must be a PEM private key.");
  }
  return cleaned.endsWith("\n") ? cleaned : `${cleaned}\n`;
}

function derLength(length: number): number[] {
  if (length < 0x80) return [length];
  if (length < 0x100) return [0x81, length];
  if (length < 0x10000) return [0x82, length >> 8, length & 0xff];
  return [0x83, length >> 16, (length >> 8) & 0xff, length & 0xff];
}

function derWrap(tag: number, content: Uint8Array): Uint8Array {
  const header = [tag, ...derLength(content.length)];
  const out = new Uint8Array(header.length + content.length);
  out.set(header, 0);
  out.set(content, header.length);
  return out;
}

const RSA_ALGORITHM_IDENTIFIER = Uint8Array.of(
  0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00,
);

/**
 * Wrap a PKCS#1 RSAPrivateKey in a PKCS#8 PrivateKeyInfo. GitHub gives apps a
 * PKCS#1 "BEGIN RSA PRIVATE KEY" file and WebCrypto only imports PKCS#8.
 */
export function pkcs1ToPkcs8(pkcs1: Uint8Array): Uint8Array {
  const version = Uint8Array.of(0x02, 0x01, 0x00);
  const octets = derWrap(0x04, pkcs1);
  const body = new Uint8Array(version.length + RSA_ALGORITHM_IDENTIFIER.length + octets.length);
  body.set(version, 0);
  body.set(RSA_ALGORITHM_IDENTIFIER, version.length);
  body.set(octets, version.length + RSA_ALGORITHM_IDENTIFIER.length);
  return derWrap(0x30, body);
}

function base64ToBytes(value: string): Uint8Array {
  let binary: string;
  try {
    binary = atob(value);
  } catch {
    throw new GitHubAppConfigError("EGREGORE_APP_PRIVATE_KEY is not a usable RSA private key.");
  }
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

/** PKCS#8 DER bytes for either PEM flavor. Encrypted and non-RSA keys are refused. */
export function pemToPkcs8(pem: string): Uint8Array {
  const match = /-----BEGIN ((?:RSA )?PRIVATE KEY)-----([\s\S]*?)-----END \1-----/.exec(pem);
  if (!match || match[2].includes(":")) {
    // No block, an unsupported label (ENCRYPTED, EC, OPENSSH), or an encryption header.
    throw new GitHubAppConfigError("EGREGORE_APP_PRIVATE_KEY is not a usable RSA private key.");
  }
  const der = base64ToBytes(match[2].replace(/\s+/g, ""));
  return match[1] === "RSA PRIVATE KEY" ? pkcs1ToPkcs8(der) : der;
}

export async function importSigningKey(pem: string): Promise<CryptoKey> {
  const der = pemToPkcs8(normalizePem(pem));
  try {
    return await crypto.subtle.importKey(
      "pkcs8",
      der,
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["sign"],
    );
  } catch {
    throw new GitHubAppConfigError("EGREGORE_APP_PRIVATE_KEY is not a usable RSA private key.");
  }
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function base64UrlJson(value: unknown): string {
  return base64Url(new TextEncoder().encode(JSON.stringify(value)));
}

export async function signAppJwt(key: CryptoKey, appId: string, nowMs: number): Promise<string> {
  const seconds = Math.floor(nowMs / 1000);
  // Eight minutes ahead is under GitHub's ten-minute cap. iat is one minute
  // behind so a small clock skew still validates.
  const header = base64UrlJson({ alg: "RS256", typ: "JWT" });
  const payload = base64UrlJson({ iat: seconds - JWT_PAST_SKEW_S, exp: seconds + JWT_FUTURE_S, iss: appId });
  const signingInput = `${header}.${payload}`;
  const signature = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    key,
    new TextEncoder().encode(signingInput),
  );
  return `${signingInput}.${base64Url(new Uint8Array(signature))}`;
}

// ---- Scope checks (the token must be no broader than one repo) -------------

export function checkPermissions(granted: unknown): void {
  if (typeof granted !== "object" || granted === null || Array.isArray(granted)) {
    throw new GitHubAppScopeError("Refusing to start: installation token permissions were missing.");
  }
  const map = granted as Record<string, unknown>;
  const problems: string[] = [];
  for (const [name, value] of Object.entries(map)) {
    if (value === null || value === "") {
      continue;
    }
    const level = String(value);
    if (!(name in REQUESTED_PERMISSIONS)) {
      problems.push(`${name}:${level}`);
      continue;
    }
    const granted = LEVELS[level];
    const ceiling = LEVELS[REQUESTED_PERMISSIONS[name]];
    if (granted === undefined || granted > ceiling) {
      problems.push(`${name}:${level}`);
    }
  }
  for (const [name, needed] of Object.entries(REQUESTED_PERMISSIONS)) {
    const actual = map[name];
    if (actual !== undefined && actual !== null && String(actual) === needed) {
      continue;
    }
    const shown = actual !== undefined && actual !== null && actual !== "" ? String(actual) : "missing";
    const label = `${name}:${shown}`;
    if (!problems.includes(label)) {
      problems.push(label);
    }
  }
  if (problems.length > 0) {
    throw new GitHubAppScopeError(
      redactText(
        "Refusing to start: installation token permissions must be " +
          "issues:write, pull_requests:read, metadata:read. " +
          `Got ${problems.join(", ")}.`,
      ),
    );
  }
}

export function fullNames(repositories: unknown): string[] {
  const malformed = () =>
    new GitHubAppScopeError("Refusing to start: installation token repository list was malformed.");
  if (!Array.isArray(repositories)) {
    throw malformed();
  }
  return repositories.map((item) => {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw malformed();
    }
    const full = (item as Record<string, unknown>).full_name;
    if (typeof full !== "string" || !full.includes("/")) {
      throw malformed();
    }
    return full;
  });
}

export function checkRepoNames(names: readonly string[], expected: string): void {
  if (names.length === 1 && names[0].toLowerCase() === expected.toLowerCase()) {
    return;
  }
  const shown = names.length > 0 ? names.join(", ") : "(empty)";
  throw new GitHubAppScopeError(
    redactText(`Refusing to start: installation token repository list must be only ${expected}. Got ${shown}.`),
  );
}

// ---- The auth object --------------------------------------------------------

interface CachedToken {
  value: string;
  expiresAt: number;
  mintedAt: number;
}

export interface GitHubAppAuthOptions {
  api: string;
  appId: string;
  privateKey: string;
  repo: string;
  installationId?: string | null;
  now?: () => number;
  fetch: FetchLike;
  skewMs?: number;
}

function due(cached: CachedToken, nowMs: number, skewMs: number): boolean {
  if (nowMs >= cached.expiresAt) {
    return true;
  }
  // A token that expires inside the skew window is used until it actually
  // expires. Refreshing it immediately would mint on every call.
  const refreshAt = cached.expiresAt - skewMs;
  if (refreshAt <= cached.mintedAt) {
    return false;
  }
  return nowMs >= refreshAt;
}

export class GitHubAppAuth {
  readonly repo: string;
  private readonly api: string;
  private readonly appId: string;
  private readonly privateKey: string;
  private readonly owner: string;
  private readonly repoName: string;
  private installationId: string | null;
  private readonly now: () => number;
  private readonly fetchImpl: FetchLike;
  private readonly skewMs: number;
  private readonly scrubber = new Scrubber();
  private cached: CachedToken | null = null;
  private signingKey: CryptoKey | null = null;

  constructor(options: GitHubAppAuthOptions) {
    const [owner, name, ...rest] = options.repo.split("/");
    if (!owner || !name || rest.length > 0) {
      throw new GitHubAppConfigError("EGREGORE_REPO must be owner/name.");
    }
    if (!/^[0-9]+$/.test(options.appId)) {
      throw new GitHubAppConfigError("EGREGORE_APP_ID must be the numeric GitHub App id.");
    }
    if (options.installationId != null && !/^[0-9]+$/.test(options.installationId)) {
      throw new GitHubAppConfigError("EGREGORE_APP_INSTALLATION_ID must be a numeric installation id.");
    }
    this.api = options.api.replace(/\/+$/, "");
    this.appId = options.appId;
    this.repo = options.repo;
    this.owner = owner;
    this.repoName = name;
    this.installationId = options.installationId ?? null;
    this.privateKey = options.privateKey;
    this.now = options.now ?? Date.now;
    this.fetchImpl = options.fetch;
    this.skewMs = options.skewMs ?? REFRESH_SKEW_MS;
    this.scrubber.rememberKey(options.privateKey);
  }

  /**
   * Returns a cached installation token, refreshing inside the skew window.
   * Every GitHub call this makes takes a unit from `budget`. A scope failure
   * is never papered over with a cached token.
   */
  async token(budget: SubrequestBudget): Promise<string> {
    const now = this.now();
    const cached = this.cached;
    if (cached !== null && !due(cached, now, this.skewMs)) {
      return cached.value;
    }
    let minted: CachedToken;
    try {
      minted = await this.mint(now, budget);
    } catch (error) {
      if (error instanceof GitHubAppScopeError || error instanceof GitHubAppConfigError) {
        throw error;
      }
      if (cached !== null && now < cached.expiresAt) {
        return cached.value;
      }
      if (error instanceof GitHubAppError) {
        throw new GitHubError(error.status, error.message);
      }
      throw error;
    }
    this.cached = minted;
    return minted.value;
  }

  private async mint(now: number, budget: SubrequestBudget): Promise<CachedToken> {
    const jwt = await this.appJwt(now);
    const installationId = await this.installationIdFor(jwt, budget);
    const { payload } = await this.http(budget, {
      method: "POST",
      url: `${this.api}/app/installations/${installationId}/access_tokens`,
      bearer: jwt,
      body: { repositories: [this.repoName], permissions: { ...REQUESTED_PERMISSIONS } },
    });
    if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
      throw new GitHubAppError("GitHub installation token response was not an object.", 502);
    }
    const record = payload as Record<string, unknown>;
    const rawToken = record.token;
    if (typeof rawToken !== "string" || !rawToken) {
      throw new GitHubAppError("GitHub installation token response had no token.", 502);
    }
    this.scrubber.remember(rawToken);
    const expiresText = typeof record.expires_at === "string" ? record.expires_at.trim() : "";
    const expires = expiresText ? tryParseTime(expiresText) : null;
    if (expires === null) {
      throw new GitHubAppError("GitHub installation token response had no expires_at.", 502);
    }
    if (expires <= now) {
      throw new GitHubAppError("GitHub returned an installation token that is already expired.", 502);
    }
    await this.refuseBroadToken(record, rawToken, budget);
    return { value: rawToken, expiresAt: expires, mintedAt: now };
  }

  private async installationIdFor(jwt: string, budget: SubrequestBudget): Promise<string> {
    if (this.installationId) {
      return this.installationId;
    }
    const owner = encodeURIComponent(this.owner);
    const name = encodeURIComponent(this.repoName);
    const { payload } = await this.http(budget, {
      method: "GET",
      url: `${this.api}/repos/${owner}/${name}/installation`,
      bearer: jwt,
    });
    const found =
      typeof payload === "object" && payload !== null && !Array.isArray(payload)
        ? (payload as Record<string, unknown>).id
        : undefined;
    if (typeof found !== "number" || !Number.isInteger(found) || found < 1) {
      throw new GitHubAppError("GitHub did not return an installation id for this repository.", 502);
    }
    this.installationId = String(found);
    return this.installationId;
  }

  private async appJwt(now: number): Promise<string> {
    try {
      this.signingKey ??= await importSigningKey(this.privateKey);
      const jwt = await signAppJwt(this.signingKey, this.appId, now);
      this.scrubber.remember(jwt);
      return jwt;
    } catch (error) {
      if (error instanceof GitHubAppConfigError) {
        throw error;
      }
      throw new GitHubAppConfigError("EGREGORE_APP_PRIVATE_KEY is not a usable RSA private key.");
    }
  }

  private async refuseBroadToken(
    payload: Record<string, unknown>,
    rawToken: string,
    budget: SubrequestBudget,
  ): Promise<void> {
    checkPermissions(payload.permissions);
    const selection = payload.repository_selection ? String(payload.repository_selection) : "";
    if (selection === "all") {
      throw new GitHubAppScopeError(
        "Refusing to start: installation token repository_selection is all, " +
          `which is broader than ${this.repo}.`,
      );
    }
    const names =
      "repositories" in payload
        ? fullNames(payload.repositories)
        : await this.listInstallationRepos(rawToken, budget);
    checkRepoNames(names, this.repo);
  }

  private async listInstallationRepos(rawToken: string, budget: SubrequestBudget): Promise<string[]> {
    const names: string[] = [];
    let url: string | null = `${this.api}/installation/repositories?per_page=100`;
    let pages = 0;
    while (url !== null) {
      pages += 1;
      if (pages > 10) {
        throw new GitHubAppScopeError("Refusing to start: installation repository list did not end.");
      }
      const result = await this.http(budget, { method: "GET", url, bearer: rawToken });
      const payload = result.payload;
      if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
        throw new GitHubAppScopeError("Refusing to start: installation repository list was malformed.");
      }
      const record = payload as Record<string, unknown>;
      names.push(...fullNames(record.repositories));
      url = result.next;
      if (url !== null && !sameOrigin(this.api, url)) {
        throw new GitHubAppScopeError("Refusing to start: installation repository list left the GitHub API.");
      }
      const total = record.total_count;
      if (url === null && typeof total === "number" && Number.isInteger(total) && total > names.length) {
        throw new GitHubAppScopeError(
          `Refusing to start: installation token reports ${total} repositories, ` +
            `which is broader than ${this.repo}.`,
        );
      }
    }
    return names;
  }

  private async http(
    budget: SubrequestBudget,
    request: { method: "GET" | "POST"; url: string; bearer: string; body?: unknown },
  ): Promise<{ payload: unknown; next: string | null }> {
    let response;
    try {
      response = await send({
        fetch: this.fetchImpl,
        budget,
        scrubber: this.scrubber,
        ...request,
      });
    } catch (error) {
      if (error instanceof SubrequestBudgetError) {
        throw error;
      }
      if (error instanceof GitHubError) {
        throw new GitHubAppError(error.message, error.status);
      }
      throw error;
    }
    if (response.status < 200 || response.status > 299) {
      throw new GitHubAppError(safeError(this.scrubber, response.status, response.text), response.status);
    }
    if (!response.text) {
      throw new GitHubAppError("GitHub HTTP 502: empty authentication response", 502);
    }
    let payload: unknown;
    try {
      payload = JSON.parse(response.text);
    } catch {
      throw new GitHubAppError("GitHub HTTP 502: authentication response was not JSON", 502);
    }
    return { payload, next: nextLink(response.link) };
  }
}
