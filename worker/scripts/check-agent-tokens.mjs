#!/usr/bin/env node
// Check an EGREGORE_AGENT_TOKENS value with the same parser the Worker runs.
// The Worker never says why a config was rejected, because that would tell a
// caller about the secret. Run this instead, before you set the secret.
//
//   node --experimental-strip-types worker/scripts/check-agent-tokens.mjs < tokens.json
//   node --experimental-strip-types worker/scripts/check-agent-tokens.mjs tokens.json
//   node --experimental-strip-types worker/scripts/check-agent-tokens.mjs --verify < tokens.json
//
// With --verify it also reads one bearer token from the TOKEN environment
// variable and says which agent, if any, that token is. It never prints the
// token or any hash. Reads only: no network, no file written.
//
// Node 22.6 or later is needed to load src/auth.ts. Newer Node versions strip
// types without the flag.

import { readFileSync } from "node:fs";
import { authenticateToken, parseTokenConfig } from "../src/auth.ts";

const argv = process.argv.slice(2);
const verify = argv.includes("--verify");
const files = argv.filter((item) => item !== "--verify");
if (files.length > 1 || files.some((item) => item.startsWith("--"))) {
  process.stderr.write("Usage: check-agent-tokens.mjs [--verify] [file]   (stdin when no file)\n");
  process.exit(2);
}

const raw = readFileSync(files.length === 1 ? files[0] : 0, "utf8");
const config = parseTokenConfig(raw);
if (!config.ok) {
  process.stderr.write("The Worker would reject this config, so every tool would answer `unauthenticated`:\n");
  for (const problem of config.problems) process.stderr.write(`  - ${problem}\n`);
  process.exit(1);
}

const summary = config.records.map((record, position) => {
  const { principal } = record;
  const tools = principal.tools === null ? "all tools" : `${principal.tools.size} tools`;
  return `  ${position}: ${principal.agent}  ${[...principal.permissions].join("+")}  ${[...principal.repos].join(",")}  ${tools}`;
});
process.stdout.write(`OK: ${config.records.length} record(s)\n${summary.join("\n")}\n`);

if (verify) {
  const token = process.env.TOKEN ?? "";
  const principal = await authenticateToken(token === "" ? null : token, config);
  if (principal === null) {
    process.stdout.write("TOKEN matches no record.\n");
    process.exit(1);
  }
  process.stdout.write(`TOKEN matches a record for ${principal.agent}.\n`);
}
