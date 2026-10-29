/**
 * Read-only specialist catalog pointers: a port of mcp/egregore_mcp/specialists.py.
 *
 * The catalog is mcp/specialists.json, bundled at build time so the Python
 * server and this Worker read the same reviewed file. Specialists are prompts in
 * the user's wshobson/agents fork. This code never fetches, runs, or installs
 * them. It returns links pinned to a commit.
 */
import catalogFile from "../../mcp/specialists.json";
import type { Json } from "./coordination";

export type Envelope = {
  ok: boolean;
  result: Json | null;
  error: { code: string; message: string; details: Json } | null;
};

export function failure(code: string, message: string, details: Json = {}): Envelope {
  return { ok: false, result: null, error: { code, message, details } };
}

const SLUG = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const SHA = /^[0-9a-f]{40}$/;
const ALLOWED_KINDS = new Set(["agent", "skill"]);
const PONYTAIL = "DietrichGebert/ponytail";
const PONYTAIL_COMMIT = "09c434ab73ca43761c5d8b0c5d341926c2fba710";

const isObject = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** urllib.parse.quote(path, safe="/") */
function quotePath(path: string): string {
  return path
    .split("/")
    .map((segment) =>
      encodeURIComponent(segment).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`),
    )
    .join("/");
}

export function getSpecialists(
  role = "",
  kind = "",
  specialistId = "",
  catalog: unknown = catalogFile,
): Envelope {
  for (const [field, value] of [
    ["role", role],
    ["kind", kind],
    ["specialist_id", specialistId],
  ] as const) {
    if (typeof value !== "string" || (value && !SLUG.test(value))) {
      return failure("invalid_input", `${field} must be an empty value or a lowercase slug.`);
    }
  }
  if (kind && !ALLOWED_KINDS.has(kind)) {
    return failure("invalid_input", "kind must be agent or skill.");
  }
  if (!isObject(catalog)) {
    return failure("unconfigured", "The pinned specialist catalog is unavailable.");
  }

  const fork = catalog.fork_repository;
  const commit = catalog.fork_commit;
  const entries = catalog.entries;
  if (
    typeof fork !== "string" ||
    fork !== "AStrasler/agents" ||
    typeof commit !== "string" ||
    !SHA.test(commit) ||
    !Array.isArray(entries)
  ) {
    return failure("unconfigured", "The pinned specialist catalog is invalid.");
  }

  const selected: Json[] = [];
  for (const item of entries) {
    if (!isObject(item)) {
      return failure("unconfigured", "The specialist catalog has an invalid entry.");
    }
    for (const key of ["id", "kind", "role", "plugin", "path"]) {
      if (typeof item[key] !== "string") {
        return failure("unconfigured", "The specialist catalog has a missing entry field.");
      }
    }
    const entryId = item.id as string;
    const entryKind = item.kind as string;
    const entryRole = item.role as string;
    const plugin = item.plugin as string;
    const path = item.path as string;
    const sourceRepo: unknown = "source_repository" in item ? item.source_repository : fork;
    const sourceCommit: unknown = "source_commit" in item ? item.source_commit : commit;
    if (
      !SLUG.test(entryId) ||
      !SLUG.test(entryRole) ||
      !SLUG.test(plugin) ||
      !ALLOWED_KINDS.has(entryKind) ||
      typeof sourceRepo !== "string" ||
      typeof sourceCommit !== "string" ||
      !SHA.test(sourceCommit) ||
      path.split("/").some((segment) => segment === "" || segment === "." || segment === "..") ||
      path.includes("\\") ||
      path.includes(":")
    ) {
      return failure("unconfigured", "The specialist catalog contains an unsafe entry.");
    }

    // External prompts are intentionally opt-in and source-pinned. Adding a
    // repository to the catalog is a reviewed code change.
    if (sourceRepo === fork) {
      const segments = path.split("/");
      const allowedPath =
        segments.length >= 4 &&
        segments[0] === "plugins" &&
        segments[1] === plugin &&
        ((entryKind === "agent" &&
          segments.length === 4 &&
          segments[2] === "agents" &&
          segments[3].endsWith(".md")) ||
          (entryKind === "skill" &&
            segments.length === 5 &&
            segments[2] === "skills" &&
            segments[4] === "SKILL.md"));
      if (sourceCommit !== commit || !allowedPath) {
        return failure("unconfigured", "The fork specialist path or commit is invalid.");
      }
    } else if (sourceRepo === PONYTAIL) {
      if (
        entryId !== "ponytail" ||
        plugin !== "ponytail" ||
        entryKind !== "skill" ||
        entryRole !== "code-implementer" ||
        path !== "skills/ponytail/SKILL.md" ||
        sourceCommit !== PONYTAIL_COMMIT
      ) {
        return failure("unconfigured", "The Ponytail source is not the reviewed pinned skill.");
      }
    } else {
      return failure("unconfigured", "The catalog contains an unapproved source repository.");
    }

    if (role && entryRole !== role) continue;
    if (kind && entryKind !== kind) continue;
    if (specialistId && entryId !== specialistId) continue;
    selected.push({
      id: entryId,
      kind: entryKind,
      role: entryRole,
      plugin,
      path,
      source_repository: sourceRepo,
      source_commit: sourceCommit,
      source_url: `https://github.com/${sourceRepo}/blob/${sourceCommit}/${quotePath(path)}`,
    });
  }

  return {
    ok: true,
    result: {
      specialists: selected,
      count: selected.length,
      fork_repository: fork,
      fork_commit: commit,
      fork_pr: catalog.fork_pr ?? null,
      upstream_repository: catalog.upstream_repository ?? null,
      upstream_commit: catalog.upstream_commit ?? null,
      capability: "catalog-only",
      note:
        "These are opt-in reference links, not callable agents. A Cursor/Codex " +
        "client must load the chosen prompt with its own permissions. " +
        "No cross-agent communication or new access is granted.",
    },
    error: null,
  };
}
