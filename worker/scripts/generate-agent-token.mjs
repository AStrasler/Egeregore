#!/usr/bin/env node
// Make a bearer token for one agent, and the record that goes into the
// EGREGORE_AGENT_TOKENS secret. Runs locally. It calls no network, reads no
// file, and writes no file.
//
//   node worker/scripts/generate-agent-token.mjs --agent cursor
//   node worker/scripts/generate-agent-token.mjs --agent codex --permissions read,review \
//        --repos AStrasler/Egeregore --tools get_tasks,get_task,request_review --label "codex laptop"
//   node worker/scripts/generate-agent-token.mjs --agent cursor --json
//   printf '%s' "$TOKEN" | node worker/scripts/generate-agent-token.mjs --hash-only
//
// The token is printed once. Hand it to the agent's client config and keep it
// out of git, chat and logs. Only the hash goes into the Worker secret, so the
// Worker cannot show the token again. To rotate, make a new token, add its
// record next to the old one, switch the client, then remove the old record.

import { createHash, randomBytes } from "node:crypto";

const AGENTS = ["chatgpt", "grokbot", "cursor", "codex"];
const PERMISSIONS = ["read", "write", "review"];
const DEFAULT_REPO = "AStrasler/Egeregore";

function usage(code) {
  const text = [
    "Usage: generate-agent-token.mjs --agent <name> [--permissions read,write,review] [--repos owner/name[,..]]",
    "                                [--tools '*' | name[,..]] [--label text] [--json]",
    "       generate-agent-token.mjs --hash-only   (reads a token on stdin, prints its sha256)",
    `Agents: ${AGENTS.join(", ")}. Permissions: ${PERMISSIONS.join(", ")}.`,
    `Defaults: all three permissions, repo ${DEFAULT_REPO}, all tools.`,
  ].join("\n");
  (code === 0 ? process.stdout : process.stderr).write(`${text}\n`);
  process.exit(code);
}

function parse(argv) {
  const out = { json: false, hashOnly: false };
  const takesValue = new Set(["--agent", "--permissions", "--repos", "--tools", "--label"]);
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--json") out.json = true;
    else if (flag === "--hash-only") out.hashOnly = true;
    else if (flag === "--help" || flag === "-h") usage(0);
    else if (takesValue.has(flag)) {
      index += 1;
      if (index >= argv.length) usage(2);
      out[flag.slice(2)] = argv[index];
    } else usage(2);
  }
  return out;
}

const sha256 = (text) => createHash("sha256").update(text).digest("hex");
const list = (value) => value.split(",").map((item) => item.trim()).filter(Boolean);

async function readStdin() {
  let text = "";
  for await (const chunk of process.stdin) text += chunk;
  return text.replace(/\r?\n$/, "");
}

const args = parse(process.argv.slice(2));

if (args.hashOnly) {
  const token = await readStdin();
  if (token.length < 32 || token.length > 256) {
    process.stderr.write("The token must be 32 to 256 characters.\n");
    process.exit(2);
  }
  process.stdout.write(`${sha256(token)}\n`);
  process.exit(0);
}

if (!AGENTS.includes(args.agent)) {
  process.stderr.write(`--agent must be one of: ${AGENTS.join(", ")}\n`);
  usage(2);
}
const permissions = list(args.permissions ?? PERMISSIONS.join(","));
const repos = list(args.repos ?? DEFAULT_REPO);
const tools = list(args.tools ?? "*");
if (permissions.length === 0 || !permissions.every((item) => PERMISSIONS.includes(item))) {
  process.stderr.write(`--permissions must be a list drawn from: ${PERMISSIONS.join(", ")}\n`);
  process.exit(2);
}

const token = `egr_${randomBytes(32).toString("base64url")}`;
const record = { sha256: sha256(token), agent: args.agent, permissions, repos, tools };
if (args.label) record.label = args.label;

if (args.json) {
  process.stdout.write(`${JSON.stringify({ token, record })}\n`);
} else {
  process.stdout.write(
    [
      "Token (shown once. Give it to the agent's client. Never commit it or paste it into chat):",
      token,
      "",
      "Record to add to the EGREGORE_AGENT_TOKENS array (safe to store: it holds only the hash):",
      JSON.stringify(record, null, 2),
      "",
      "Check the whole array before you set the secret:",
      "  node --experimental-strip-types worker/scripts/check-agent-tokens.mjs < tokens.json",
      "",
    ].join("\n"),
  );
}
