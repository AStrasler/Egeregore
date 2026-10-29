#!/usr/bin/env node
// Make the whole set of agent tokens in one run, for the deploy runbook.
// Runs on the box. It calls no network.
//
//   node --experimental-strip-types worker/scripts/provision-agent-tokens.mjs --out-dir ~/.egregore-secrets/2026-10-initial
//   node --experimental-strip-types worker/scripts/provision-agent-tokens.mjs --out-dir DIR --plan plan.json
//
// It runs generate-agent-token.mjs once per planned token, then runs the
// Worker's own parser (check-agent-tokens.mjs) over the records, and only then
// writes two files into DIR (mode 0700, files 0600):
//
//   agent-tokens.private.json  The raw tokens and what each may do. Keep it on
//                              the box. The smoke test reads it. Never commit
//                              it, never print it, never paste it into chat.
//   agent-tokens.records.json  The records with only hashes. This is the exact
//                              value of the EGREGORE_AGENT_TOKENS secret.
//
// It refuses a directory inside a git work tree, so a token cannot be staged by
// accident, and it never overwrites a file. To rotate, use a new directory.
// Its output names the agents and permissions. It never prints a token.

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const GENERATE = join(HERE, "generate-agent-token.mjs");
const CHECK = join(HERE, "check-agent-tokens.mjs");
const REPO_ROOT = resolve(HERE, "..", "..");

// The plan Aaron confirms in the runbook before this runs. Change it with --plan.
// `request_review` posts a comment that makes Codex run, so `review` goes only to
// the agents that open pull requests.
const DEFAULT_PLAN = [
  { agent: "chatgpt", permissions: ["read"] },
  { agent: "grokbot", permissions: ["read", "write", "review"] },
  { agent: "cursor", permissions: ["read", "write", "review"] },
  { agent: "codex", permissions: ["read", "write"] },
];

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(2);
}

function parse(argv) {
  const out = {};
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--out-dir" || flag === "--plan") {
      index += 1;
      if (index >= argv.length) fail(`${flag} needs a value`);
      out[flag.slice(2)] = argv[index];
    } else if (flag === "--help" || flag === "-h") {
      process.stdout.write("Usage: provision-agent-tokens.mjs --out-dir DIR [--plan plan.json]\n");
      process.exit(0);
    } else fail(`Unknown option ${flag}`);
  }
  if (!out["out-dir"]) fail("--out-dir is required");
  return out;
}

/** The nearest directory that exists, so a new directory can be checked before it is made. */
function nearestExisting(path) {
  let current = path;
  while (!existsSync(current)) {
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return current;
}

function insideGitWorkTree(path) {
  try {
    const out = execFileSync("git", ["-C", nearestExisting(path), "rev-parse", "--is-inside-work-tree"], {
      stdio: ["ignore", "pipe", "ignore"],
    });
    return out.toString().trim() === "true";
  } catch {
    return false; // not a work tree, or git is not installed
  }
}

function prepareDirectory(path) {
  if (!isAbsolute(path)) fail("--out-dir must be an absolute path");
  const dir = resolve(path);
  if (dir === REPO_ROOT || dir.startsWith(REPO_ROOT + sep) || insideGitWorkTree(dir)) {
    fail("--out-dir is inside a git work tree. Put the tokens outside any repository.");
  }
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true, mode: 0o700 });
  }
  const info = statSync(dir);
  if (!info.isDirectory()) fail("--out-dir is not a directory");
  if (typeof process.getuid === "function" && info.uid !== process.getuid()) fail("--out-dir is owned by another user");
  if ((info.mode & 0o077) !== 0) fail("--out-dir must not be readable or writable by group or others (use mode 0700)");
  return dir;
}

function loadPlan(file) {
  if (file === undefined) return DEFAULT_PLAN;
  let plan;
  try {
    plan = JSON.parse(readFileSync(file, "utf8"));
  } catch {
    fail("--plan is not a readable JSON file");
  }
  const tokens = Array.isArray(plan) ? plan : plan?.tokens;
  if (!Array.isArray(tokens) || tokens.length === 0) fail("--plan must be a non-empty array, or {\"tokens\": [...]}");
  return tokens;
}

function generate(entry, stamp) {
  const args = [GENERATE, "--agent", String(entry.agent), "--json"];
  if (entry.permissions !== undefined) args.push("--permissions", [].concat(entry.permissions).join(","));
  if (entry.repos !== undefined) args.push("--repos", [].concat(entry.repos).join(","));
  if (entry.tools !== undefined) args.push("--tools", [].concat(entry.tools).join(","));
  args.push("--label", entry.label ?? `${entry.agent}-${stamp}`);
  let out;
  try {
    out = execFileSync(process.execPath, args, { stdio: ["ignore", "pipe", "pipe"] }).toString();
  } catch (error) {
    // The generator prints a reason on stderr when it refuses, and no token.
    fail(`generate-agent-token.mjs refused ${JSON.stringify(entry.agent)}: ${String(error.stderr ?? "").trim().split("\n")[0]}`);
  }
  const { token, record } = JSON.parse(out);
  return {
    token,
    record,
    private: {
      agent: record.agent,
      label: record.label,
      permissions: record.permissions,
      repos: record.repos,
      tools: record.tools,
      token,
    },
  };
}

function checkWithWorkerParser(records) {
  try {
    return execFileSync(process.execPath, ["--experimental-strip-types", CHECK], {
      input: JSON.stringify(records),
      stdio: ["pipe", "pipe", "pipe"],
    }).toString();
  } catch (error) {
    fail(`The Worker's parser rejected the records, so nothing was written:\n${String(error.stderr ?? "").trim()}`);
  }
}

const args = parse(process.argv.slice(2));
const plan = loadPlan(args.plan);
const dir = prepareDirectory(args["out-dir"]);
const privatePath = join(dir, "agent-tokens.private.json");
const recordsPath = join(dir, "agent-tokens.records.json");
for (const path of [privatePath, recordsPath]) {
  if (existsSync(path)) fail(`${path} already exists. Use a new --out-dir to rotate. Nothing was overwritten.`);
}

const stamp = new Date().toISOString().slice(0, 10).replaceAll("-", "");
const made = plan.map((entry) => generate(entry, stamp));
const records = made.map((item) => item.record);
const verdict = checkWithWorkerParser(records);

writeFileSync(recordsPath, `${JSON.stringify(records, null, 2)}\n`, { mode: 0o600, flag: "wx" });
writeFileSync(privatePath, `${JSON.stringify({ version: 1, tokens: made.map((item) => item.private) }, null, 2)}\n`, {
  mode: 0o600,
  flag: "wx",
});

process.stdout.write(`${verdict}Wrote ${made.length} token(s). No token was printed.\n  ${privatePath}  (raw tokens, mode 0600)\n  ${recordsPath}  (hashes only, the EGREGORE_AGENT_TOKENS value)\n`);
