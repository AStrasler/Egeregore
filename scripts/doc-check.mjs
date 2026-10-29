#!/usr/bin/env node
// Doc rule checker. Node only, no dependencies.
//
//   node scripts/doc-check.mjs check [--format text|github] [path ...]
//   node scripts/doc-check.mjs validate [--rules-dir DIR]
//   node scripts/doc-check.mjs list
//
// One rule per file in scripts/doc-rules/. A rule file carries its own tests:
// every line under "## Fails" must be flagged by the rule and every line under
// "## Passes" must not be. `validate` runs those examples through the same
// pipeline `check` uses, and scripts/doc-check.test.mjs runs `validate` in CI.
// The rules and the waiver syntax are described in docs/DOC-RULES.md.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, "..");
export const RULES_DIR = path.join(HERE, "doc-rules");

const SEVERITIES = new Set(["block", "warn"]);
const SCANS = new Set(["all", "prose"]);
const MIN_WAIVER_REASON = 10;

// Placeholders let a rule file show a secret-shaped example without storing one.
// Each is assembled from parts here, and only expanded in memory.
export const PLACEHOLDERS = {
  "{{ghp}}": ["ghp", "_", "a".repeat(36)].join(""),
  "{{ghs}}": ["ghs", "_", "b".repeat(36)].join(""),
  "{{github-pat}}": ["github", "_pat_", "C".repeat(30)].join(""),
  "{{sk}}": ["s", "k-", "d".repeat(30)].join(""),
  "{{slack}}": ["xo", "xb-", "1234567890-canary"].join(""),
  "{{aws}}": ["AK", "IA", "CANARY0123456789"].join(""),
  "{{pem}}": ["-----BEGIN ", "RSA PRIVATE KEY-----"].join(""),
  "{{jwt}}": ["eyJ", "hbGciOiJSUzI1NiJ9", ".", "eyJpc3MiOiJjYW5hcnkifQ", ".", "c2lnbmF0dXJlMDEyMzQ1Njc4OQ"].join(""),
  "{{bearer}}": ["Bearer ", "canary0123456789ABCDEFGHIJ"].join(""),
};

export function expand(text) {
  let out = text;
  for (const [key, value] of Object.entries(PLACEHOLDERS)) out = out.split(key).join(value);
  return out;
}

// ------------------------------------------------------------ rule files

function unquote(raw) {
  const text = raw.trim();
  if (text.length >= 2 && text.startsWith("'") && text.endsWith("'")) return text.slice(1, -1).replace(/''/g, "'");
  if (text.length >= 2 && text.startsWith('"') && text.endsWith('"')) return text.slice(1, -1).replace(/\\(["\\])/g, "$1");
  return text;
}

function readFrontmatter(lines) {
  const meta = {};
  let listKey = null;
  for (const line of lines) {
    if (!line.trim()) continue;
    const bullet = /^\s+-\s+(.*)$/.exec(line);
    if (bullet) {
      if (!listKey) throw new Error(`list item with no key: ${line}`);
      meta[listKey].push(unquote(bullet[1]));
      continue;
    }
    const pair = /^([a-z_]+):\s*(.*)$/.exec(line);
    if (!pair) throw new Error(`cannot read frontmatter line: ${line}`);
    if (pair[2] === "") {
      meta[pair[1]] = [];
      listKey = pair[1];
    } else {
      meta[pair[1]] = unquote(pair[2]);
      listKey = null;
    }
  }
  return meta;
}

function bullets(block) {
  return block
    .split("\n")
    .map((line) => /^\s*-\s+(.*\S)\s*$/.exec(line))
    .filter(Boolean)
    .map((m) => m[1]);
}

export function parseRule(file) {
  const text = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
  const split = /^---\n([\s\S]*?)\n---\n([\s\S]*)$/.exec(text);
  if (!split) throw new Error("no frontmatter block");
  const meta = readFrontmatter(split[1].split("\n"));
  const body = split[2];
  const failsAt = body.search(/^## Fails\s*$/m);
  const passesAt = body.search(/^## Passes\s*$/m);
  const section = (from, to) => body.slice(from, to > from ? to : undefined);
  return {
    file,
    id: path.basename(file, ".md"),
    meta,
    explanation: (failsAt >= 0 ? body.slice(0, failsAt) : body).trim(),
    fails: failsAt >= 0 ? bullets(section(failsAt, passesAt)) : null,
    passes: passesAt >= 0 ? bullets(section(passesAt, failsAt)) : null,
  };
}

/** Turns a parsed rule into a runnable one, or returns the problems with it. */
export function compile(rule) {
  const problems = [];
  const { meta } = rule;
  if (meta.name !== rule.id) problems.push(`name "${meta.name ?? ""}" must equal the file name "${rule.id}"`);
  if (!meta.description) problems.push("description is missing");
  if (!SEVERITIES.has(meta.severity)) problems.push(`severity must be block or warn, not "${meta.severity ?? ""}"`);
  const scan = meta.scan ?? "all";
  if (!SCANS.has(scan)) problems.push(`scan must be all or prose, not "${scan}"`);
  if (!meta.fix) problems.push("fix is missing: say what to do instead");
  if (rule.fails === null || rule.fails.length === 0) problems.push("needs at least one line under ## Fails");
  if (rule.passes === null || rule.passes.length === 0) problems.push("needs at least one line under ## Passes");
  const regexes = [];
  if (!Array.isArray(meta.patterns) || meta.patterns.length === 0) {
    problems.push("patterns must list at least one regular expression");
  } else {
    const flags = meta.ignore_case === "true" ? "giu" : "gu";
    for (const source of meta.patterns) {
      try {
        regexes.push(new RegExp(source, flags));
      } catch (error) {
        problems.push(`pattern does not compile: ${source} (${error.message})`);
      }
    }
  }
  return {
    ...rule,
    problems,
    scan,
    severity: meta.severity,
    showMatch: meta.show_match !== "false",
    regexes,
  };
}

export function loadRules(dir = RULES_DIR) {
  if (!fs.existsSync(dir)) throw new Error(`rules directory not found: ${dir}`);
  return fs
    .readdirSync(dir)
    .filter((name) => name.endsWith(".md") && name !== "README.md")
    .sort()
    .map((name) => {
      const file = path.join(dir, name);
      try {
        return compile(parseRule(file));
      } catch (error) {
        return { file, id: path.basename(name, ".md"), problems: [error.message], regexes: [], fails: [], passes: [], meta: {} };
      }
    });
}

// ------------------------------------------------------------- scanning

/** Blank out what is not prose, keeping every line number. */
export function proseOnly(lines, { keepComments = false } = {}) {
  const out = [];
  let fence = null;
  for (const raw of lines) {
    const mark = /^\s{0,3}(`{3,}|~{3,})/.exec(raw);
    if (fence) {
      if (mark && mark[1][0] === fence.char && mark[1].length >= fence.length) fence = null;
      out.push("");
      continue;
    }
    if (mark) {
      fence = { char: mark[1][0], length: mark[1].length };
      out.push("");
      continue;
    }
    const cleaned = raw.replace(/`[^`]*`/g, " ");
    out.push(keepComments ? cleaned : cleaned.replace(/<!--.*?-->/g, " ").replace(/https?:\/\/\S+/g, " "));
  }
  return out;
}

const WAIVER = /<!--\s*doc-check-allow\s+([a-z0-9-]+)\s*:\s*(.*?)\s*-->/;

/** Waivers by line: a marker excuses its own line and the line after it. A marker inside a code span or a fenced block is only an example. */
export function readWaivers(lines) {
  const waivers = [];
  const bad = [];
  lines.forEach((line, index) => {
    const found = WAIVER.exec(line);
    if (!found) return;
    const [, ruleName, reason] = found;
    if (reason.length < MIN_WAIVER_REASON) {
      bad.push({ line: index + 1, ruleName });
      return;
    }
    waivers.push({ line: index + 1, ruleName, reason });
  });
  return { waivers, bad };
}

/** Findings for one document's text. Pure: the same text and rules give the same findings. */
export function checkText(text, rules, label = "<text>") {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const prose = proseOnly(lines);
  const { waivers, bad } = readWaivers(proseOnly(lines, { keepComments: true }));
  const known = new Set(rules.map((rule) => rule.id));
  for (const item of waivers) {
    if (!known.has(item.ruleName)) bad.push({ line: item.line, ruleName: item.ruleName, unknown: true });
  }
  const findings = bad.map((item) => ({
    file: label,
    line: item.line,
    column: 1,
    rule: "doc-check-waiver",
    severity: "block",
    message: item.unknown
      ? `waiver names a rule that does not exist: ${item.ruleName}`
      : `waiver for ${item.ruleName} needs a reason of at least ${MIN_WAIVER_REASON} characters`,
    fix: "write <!-- doc-check-allow rule-name: why this line is fine -->",
    shown: null,
  }));
  for (const rule of rules) {
    if (rule.problems.length > 0) continue;
    const source = rule.scan === "prose" ? prose : lines;
    source.forEach((line, index) => {
      if (!line) return;
      const lineNo = index + 1;
      const waived = waivers.some((w) => w.ruleName === rule.id && (w.line === lineNo || w.line === lineNo - 1));
      if (waived) return;
      for (const regex of rule.regexes) {
        regex.lastIndex = 0;
        for (const match of line.matchAll(regex)) {
          findings.push({
            file: label,
            line: lineNo,
            column: (match.index ?? 0) + 1,
            rule: rule.id,
            severity: rule.severity,
            message: rule.meta.description,
            fix: rule.meta.fix,
            shown: rule.showMatch ? match[0] : null,
            length: match[0].length,
          });
        }
      }
    });
  }
  return findings.sort((a, b) => a.line - b.line || a.column - b.column);
}

// --------------------------------------------------------- the rule tests

/** Runs a rule's own examples. Returns one result per example. */
export function exampleResults(rule) {
  const results = [];
  for (const [kind, list] of [["fails", rule.fails ?? []], ["passes", rule.passes ?? []]]) {
    list.forEach((example, index) => {
      const hits = checkText(expand(example), [rule]).filter((f) => f.rule === rule.id);
      results.push({ kind, index, example, hits: hits.length, ok: kind === "fails" ? hits.length > 0 : hits.length === 0 });
    });
  }
  return results;
}

/** Describes every way a rules directory is wrong. Examples are shown unexpanded. */
export function validate(rules) {
  const errors = [];
  const names = new Set();
  for (const rule of rules) {
    const file = path.basename(rule.file);
    for (const problem of rule.problems) errors.push(`${file}: ${problem}`);
    if (names.has(rule.id)) errors.push(`${file}: duplicate rule name`);
    names.add(rule.id);
    if (rule.problems.length > 0) continue;
    for (const result of exampleResults(rule)) {
      if (result.ok) continue;
      errors.push(
        result.kind === "fails"
          ? `${file}: Fails example ${result.index + 1} is not flagged: ${result.example}`
          : `${file}: Passes example ${result.index + 1} is flagged: ${result.example}`,
      );
    }
  }
  return errors;
}

// ------------------------------------------------------------- targets

const SKIP_DIRS = new Set(["node_modules", ".git", "dist", ".wrangler"]);

export function defaultTargets(root = ROOT) {
  const found = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (SKIP_DIRS.has(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.(md|ya?ml)$/.test(entry.name)) found.push(full);
    }
  };
  for (const name of ["docs", "mcp", "worker", ".github"]) {
    const dir = path.join(root, name);
    if (fs.existsSync(dir)) walk(dir);
  }
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (entry.isFile() && entry.name.endsWith(".md")) found.push(path.join(root, entry.name));
  }
  return found.filter((file) => !file.includes(`${path.sep}doc-rules${path.sep}`)).sort();
}

export function checkFiles(files, rules, root = ROOT) {
  const all = [];
  for (const file of files) {
    const label = path.relative(root, file).split(path.sep).join("/");
    all.push(...checkText(fs.readFileSync(file, "utf8"), rules, label));
  }
  return all;
}

// ------------------------------------------------------------------ CLI

function show(finding) {
  const where = `${finding.file}:${finding.line}:${finding.column}`;
  const what = finding.shown === null ? `[match hidden, ${finding.length ?? "?"} characters]` : `"${finding.shown}"`;
  return `${where} ${finding.severity} ${finding.rule}: ${finding.message} ${what}\n    fix: ${finding.fix}`;
}

function github(finding) {
  const level = finding.severity === "block" ? "error" : "warning";
  const text = `${finding.rule}: ${finding.message}. ${finding.fix}`.replace(/[\r\n]+/g, " ");
  return `::${level} file=${finding.file},line=${finding.line},col=${finding.column}::${text}`;
}

export function main(argv, out = console) {
  const [command = "check", ...rest] = argv;
  let dir = RULES_DIR;
  let format = "text";
  const paths = [];
  for (let i = 0; i < rest.length; i += 1) {
    if (rest[i] === "--rules-dir") dir = path.resolve(rest[++i] ?? "");
    else if (rest[i] === "--format") format = rest[++i] ?? "text";
    else paths.push(path.resolve(rest[i]));
  }
  const rules = loadRules(dir);
  if (command === "list") {
    for (const rule of rules) out.log(`${rule.id.padEnd(28)} ${String(rule.severity ?? "?").padEnd(6)} ${rule.meta.description ?? ""}`);
    return 0;
  }
  if (command === "validate") {
    const errors = validate(rules);
    for (const error of errors) out.error(`doc-check: ${error}`);
    if (errors.length === 0) out.log(`doc-check: ${rules.length} rules valid`);
    return errors.length === 0 ? 0 : 1;
  }
  if (command === "check") {
    const invalid = validate(rules);
    if (invalid.length > 0) {
      for (const error of invalid) out.error(`doc-check: ${error}`);
      return 1;
    }
    const files = paths.length > 0 ? paths : defaultTargets();
    const findings = checkFiles(files, rules);
    for (const finding of findings) out.log(format === "github" ? github(finding) : show(finding));
    const blocking = findings.filter((f) => f.severity === "block").length;
    out.log(`doc-check: ${files.length} files, ${blocking} blocking, ${findings.length - blocking} warnings`);
    return blocking > 0 ? 1 : 0;
  }
  out.error(`doc-check: unknown command "${command}". Use check, validate or list.`);
  return 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main(process.argv.slice(2));
}
