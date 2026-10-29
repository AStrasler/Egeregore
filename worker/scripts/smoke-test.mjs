#!/usr/bin/env node
// Smoke-test a deployed Egregore Worker. Run it on the box, after the release
// steps in docs/DEPLOY-RUNBOOK.md, against a test issue made for the purpose.
//
//   node --experimental-strip-types worker/scripts/smoke-test.mjs \
//     --url https://egeregore.<subdomain>.workers.dev/mcp \
//     --tokens-file ~/.egregore-secrets/<dir>/agent-tokens.private.json \
//     --issue 123 [--pull 124] [--no-write]
//
// --issue  An open issue with Role `any`, `type:task` and `status:ready`, made
//          for this test. The write checks claim it, post progress, renew and
//          release it, one token at a time.
// --pull   An open test pull request. Needed to try request_review live, which
//          posts a review-request comment.
// --no-write  Run only the read and refusal checks. Nothing is posted.
//
// The tokens file is the private file from provision-agent-tokens.mjs. The
// script refuses a file that group or others can read, or one inside a git work
// tree. It never prints a token. The exit code is 0 only when every check passes.
// Needs Node 22.6 or later, like check-agent-tokens.mjs.

import { execFileSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { parseReply, runSmoke } from "./smoke-lib.ts";

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(2);
}

function parse(argv) {
  const out = { write: true };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--no-write") out.write = false;
    else if (["--url", "--tokens-file", "--issue", "--pull"].includes(flag)) {
      index += 1;
      if (index >= argv.length) fail(`${flag} needs a value`);
      out[flag.slice(2)] = argv[index];
    } else if (flag === "--help" || flag === "-h") {
      process.stdout.write("Usage: smoke-test.mjs --url URL --tokens-file FILE --issue N [--pull N] [--no-write]\n");
      process.exit(0);
    } else fail(`Unknown option ${flag}`);
  }
  return out;
}

const number = (name, value) => {
  if (!/^[1-9][0-9]{0,8}$/.test(String(value ?? ""))) fail(`${name} must be a positive whole number`);
  return Number(value);
};

function checkUrl(text) {
  let url;
  try {
    url = new URL(text);
  } catch {
    fail("--url is not a URL");
  }
  const local = url.hostname === "127.0.0.1" || url.hostname === "localhost";
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) fail("--url must be https (http only for localhost)");
  if (url.username !== "" || url.password !== "") fail("--url must not carry credentials");
  if (url.pathname !== "/mcp") fail("--url must end in /mcp");
  return url;
}

function readTokens(file) {
  const path = resolve(file);
  let info;
  try {
    info = statSync(path);
  } catch {
    fail("--tokens-file does not exist");
  }
  if ((info.mode & 0o077) !== 0) fail("--tokens-file must not be readable by group or others (chmod 600)");
  try {
    const inside = execFileSync("git", ["-C", dirname(path), "rev-parse", "--is-inside-work-tree"], { stdio: ["ignore", "pipe", "ignore"] });
    if (inside.toString().trim() === "true") fail("--tokens-file is inside a git work tree. Keep tokens outside any repository.");
  } catch (error) {
    if (error?.status === undefined) throw error; // fail() exits, so anything else here is a real error
  }
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    fail("--tokens-file is not valid JSON");
  }
  const list = parsed?.tokens;
  const ok =
    parsed?.version === 1 &&
    Array.isArray(list) &&
    list.length > 0 &&
    list.every((item) => typeof item?.agent === "string" && typeof item?.token === "string" && item.token.length >= 32 && Array.isArray(item?.permissions) && Array.isArray(item?.tools));
  if (!ok) fail("--tokens-file does not look like the file provision-agent-tokens.mjs writes");
  return list;
}

const args = parse(process.argv.slice(2));
if (!args.url || !args["tokens-file"] || !args.issue) fail("--url, --tokens-file and --issue are required");
const url = checkUrl(args.url);
const issue = number("--issue", args.issue);
const pull = args.pull === undefined ? undefined : number("--pull", args.pull);
const tokens = readTokens(args["tokens-file"]);

const post = async (headers, body) => {
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    redirect: "manual", // never follow a redirect with a token attached
    signal: AbortSignal.timeout(20_000),
  });
  return parseReply(response.status, response.headers.get("content-type") ?? "", await response.text());
};

process.stdout.write(`Smoke test of ${url.origin}${url.pathname}: ${tokens.length} token(s), issue #${issue}${pull === undefined ? "" : `, pull #${pull}`}${args.write ? "" : ", read-only"}\n`);
const report = await runSmoke({ post, tokens, issue, pull, write: args.write });
for (const check of report.checks) {
  if (check.status === "pass") continue; // passes are counted, failures and skips are listed
  process.stdout.write(`${check.status === "fail" ? "FAIL" : "skip"}  ${check.name}${check.detail ? `: ${check.detail}` : ""}\n`);
}
process.stdout.write(`${report.passed} passed, ${report.failed} failed, ${report.skipped} skipped\n`);
for (const line of report.cleanup) process.stdout.write(`Cleanup: ${line}\n`);
process.exit(report.ok ? 0 : 1);
