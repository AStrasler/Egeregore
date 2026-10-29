// Run with: node --test scripts/doc-check.test.mjs
// Part 1 turns every example in every shipped rule file into its own test.
// Part 2 proves the checker itself notices a broken rule, and behaves as documented.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  PLACEHOLDERS,
  checkText,
  compile,
  defaultTargets,
  checkFiles,
  exampleResults,
  expand,
  loadRules,
  parseRule,
  validate,
  proseOnly,
  ROOT,
} from "./doc-check.mjs";

const CLI = fileURLToPath(new URL("./doc-check.mjs", import.meta.url));
const shipped = loadRules();

// ---------------------------------------------------------------- part 1

test("the shipped rule set is the one the docs describe", () => {
  assert.deepEqual(
    shipped.map((rule) => rule.id),
    ["no-health-readings", "no-private-paths-or-emails", "no-token-like-strings", "one-name-per-role"],
  );
});

test("every shipped rule file is well formed", () => {
  assert.deepEqual(validate(shipped), []);
});

for (const rule of shipped) {
  for (const result of exampleResults(rule)) {
    const verb = result.kind === "fails" ? "flags" : "allows";
    test(`${rule.id} ${verb} ${result.kind} example ${result.index + 1}`, () => {
      assert.ok(result.ok, `${result.kind} example ${result.index + 1} gave ${result.hits} findings: ${result.example}`);
    });
  }
}

test("the documents in this repository pass every shipped rule", () => {
  const findings = checkFiles(defaultTargets(), shipped);
  const blocking = findings.filter((f) => f.severity === "block");
  assert.deepEqual(
    blocking.map((f) => `${f.file}:${f.line} ${f.rule}`),
    [],
  );
});

test("the rule files are not themselves scanned", () => {
  const targets = defaultTargets();
  assert.ok(targets.length > 5);
  assert.equal(targets.filter((file) => file.includes(`${path.sep}doc-rules${path.sep}`)).length, 0);
});

test("no rule file stores a secret-shaped literal", () => {
  const token = shipped.find((rule) => rule.id === "no-token-like-strings");
  for (const rule of shipped) {
    const raw = fs.readFileSync(rule.file, "utf8");
    assert.deepEqual(checkText(raw, [token]), [], `${path.basename(rule.file)} must use placeholders`);
  }
});

// ---------------------------------------------------------------- part 2

function ruleFile(dir, name, overrides = {}) {
  const meta = {
    name,
    description: "demo rule",
    severity: "block",
    fix: "do the other thing",
    patterns: ["\\bforbidden\\b"],
    fails: ["a forbidden word"],
    passes: ["a fine word"],
    ...overrides,
  };
  const head = [`name: ${meta.name}`];
  if (meta.description !== undefined) head.push(`description: ${meta.description}`);
  if (meta.severity !== undefined) head.push(`severity: ${meta.severity}`);
  if (meta.scan) head.push(`scan: ${meta.scan}`);
  if (meta.fix !== undefined) head.push(`fix: ${meta.fix}`);
  head.push("patterns:", ...meta.patterns.map((p) => `  - '${p}'`));
  const body = ["---", ...head, "---", "", "Demo.", "", "## Fails", "", ...meta.fails.map((x) => `- ${x}`), "", "## Passes", "", ...meta.passes.map((x) => `- ${x}`), ""];
  fs.writeFileSync(path.join(dir, `${name}.md`), body.join("\n"));
}

function scratch() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "doc-check-"));
}

function cli(...args) {
  return spawnSync(process.execPath, [CLI, ...args], { encoding: "utf8" });
}

test("a good fixture rule validates", () => {
  const dir = scratch();
  ruleFile(dir, "demo");
  assert.deepEqual(validate(loadRules(dir)), []);
});

test("a rule whose pattern misses its own Fails example fails by name", () => {
  const dir = scratch();
  ruleFile(dir, "too-narrow", { patterns: ["\\bnever-appears\\b"] });
  const errors = validate(loadRules(dir));
  assert.equal(errors.length, 1);
  assert.match(errors[0], /^too-narrow\.md: Fails example 1 is not flagged/);
});

test("a rule whose pattern catches its own Passes example fails by name", () => {
  const dir = scratch();
  ruleFile(dir, "too-wide", { patterns: ["word"] });
  const errors = validate(loadRules(dir));
  assert.equal(errors.length, 1);
  assert.match(errors[0], /^too-wide\.md: Passes example 1 is flagged/);
});

test("a rule file is rejected for each missing part", () => {
  const dir = scratch();
  ruleFile(dir, "no-fails", { fails: [] });
  ruleFile(dir, "no-passes", { passes: [] });
  ruleFile(dir, "no-fix", { fix: undefined });
  ruleFile(dir, "bad-severity", { severity: "maybe" });
  ruleFile(dir, "bad-scan", { scan: "everything" });
  ruleFile(dir, "bad-pattern", { patterns: ["(unclosed"] });
  const errors = validate(loadRules(dir)).join("\n");
  for (const name of ["no-fails", "no-passes", "no-fix", "bad-severity", "bad-scan", "bad-pattern"]) {
    assert.match(errors, new RegExp(`${name}\\.md: `), `${name} should be named`);
  }
});

test("the name in the file must match the file name", () => {
  const dir = scratch();
  ruleFile(dir, "real-name");
  const text = fs.readFileSync(path.join(dir, "real-name.md"), "utf8").replace("name: real-name", "name: other-name");
  fs.writeFileSync(path.join(dir, "real-name.md"), text);
  assert.match(validate(loadRules(dir)).join("\n"), /real-name\.md: name "other-name" must equal/);
});

test("a file with no frontmatter is reported, not skipped", () => {
  const dir = scratch();
  fs.writeFileSync(path.join(dir, "broken.md"), "just text\n");
  assert.match(validate(loadRules(dir)).join("\n"), /broken\.md: no frontmatter block/);
});

test("validate and check exit non-zero for a broken rules directory", () => {
  const dir = scratch();
  ruleFile(dir, "too-narrow", { patterns: ["\\bnever-appears\\b"] });
  const run = cli("validate", "--rules-dir", dir);
  assert.equal(run.status, 1);
  assert.match(run.stderr, /too-narrow\.md/);
  assert.equal(cli("check", "--rules-dir", dir).status, 1);
});

test("check exits 1 on a blocking finding and 0 on a warning", () => {
  const dir = scratch();
  ruleFile(dir, "hard");
  ruleFile(dir, "soft", { severity: "warn", patterns: ["\\bdiscouraged\\b"], fails: ["a discouraged word"], passes: ["a fine word"] });
  const hard = path.join(dir, "hard.txt");
  const soft = path.join(dir, "soft.txt");
  fs.writeFileSync(hard, "line\nthis is forbidden\n");
  fs.writeFileSync(soft, "this is discouraged\n");
  const failed = cli("check", "--rules-dir", dir, hard);
  assert.equal(failed.status, 1);
  assert.match(failed.stdout, /hard\.txt:2:9 block hard:/);
  const warned = cli("check", "--rules-dir", dir, soft);
  assert.equal(warned.status, 0);
  assert.match(warned.stdout, /warn soft:/);
});

test("the github format writes workflow annotations", () => {
  const dir = scratch();
  ruleFile(dir, "hard");
  const file = path.join(dir, "doc.txt");
  fs.writeFileSync(file, "this is forbidden\n");
  const run = cli("check", "--rules-dir", dir, "--format", "github", file);
  assert.match(run.stdout, /^::error file=.*doc\.txt,line=1,col=9::hard: demo rule\. do the other thing/m);
});

test("a secret-shaped match is never printed", () => {
  const document = `Use ${PLACEHOLDERS["{{ghp}}"]} now.\n`;
  const [finding] = checkText(document, shipped, "doc.md");
  assert.equal(finding.rule, "no-token-like-strings");
  const dir = scratch();
  const file = path.join(dir, "doc.md");
  fs.writeFileSync(file, document);
  const run = cli("check", file);
  assert.equal(run.status, 1);
  const output = run.stdout + run.stderr;
  assert.match(output, /match hidden/);
  for (const value of Object.values(PLACEHOLDERS)) assert.ok(!output.includes(value), "output repeated a secret-shaped value");
});

test("a non-secret match is shown so it can be found", () => {
  const [finding] = checkText("Grok Bot starts.\n", shipped, "doc.md");
  assert.equal(finding.rule, "one-name-per-role");
  assert.equal(finding.shown, "Grok Bot");
});

test("placeholders expand and nothing else does", () => {
  assert.equal(expand("a {{ghp}} b"), `a ${PLACEHOLDERS["{{ghp}}"]} b`);
  assert.equal(expand("{{unknown}}"), "{{unknown}}");
});

test("prose rules skip fenced blocks, code spans, comments and links", () => {
  const text = ["```", "Grok Bot", "```", "`Grok Bot` and <!-- Grok Bot --> and https://x.test/Grok-Bot", "Grok Bot"].join("\n");
  const findings = checkText(text, shipped, "doc.md");
  assert.deepEqual(findings.map((f) => f.line), [5]);
});

test("line numbers survive prose stripping", () => {
  const lines = ["a", "```js", "b", "```", "c"];
  assert.equal(proseOnly(lines).length, lines.length);
});

test("a longer fence is not closed by a shorter one", () => {
  const text = ["````", "```", "Grok Bot", "```", "````", "after"].join("\n");
  assert.deepEqual(checkText(text, shipped, "doc.md"), []);
});

test("a rule that scans everything still sees code blocks", () => {
  const text = ["```", `token ${PLACEHOLDERS["{{ghp}}"]}`, "```"].join("\n");
  assert.equal(checkText(text, shipped, "doc.md").length, 1);
});

test("a waiver with a reason excuses its own line and the next, and no more", () => {
  const waiver = "<!-- doc-check-allow one-name-per-role: quoting a product name -->";
  const same = checkText(`Grok Bot ${waiver}`, shipped, "doc.md");
  assert.deepEqual(same, []);
  const next = checkText(`${waiver}\nGrok Bot\nGrok Bot`, shipped, "doc.md");
  assert.deepEqual(next.map((f) => f.line), [3]);
});

test("a waiver is for one rule only", () => {
  const text = `<!-- doc-check-allow one-name-per-role: quoting a product name -->\nGrok Bot at ${PLACEHOLDERS["{{ghp}}"]}`;
  assert.deepEqual(checkText(text, shipped, "doc.md").map((f) => f.rule), ["no-token-like-strings"]);
});

test("a waiver without a real reason is itself a finding", () => {
  const text = "<!-- doc-check-allow one-name-per-role: ok -->\nGrok Bot";
  const rules = checkText(text, shipped, "doc.md").map((f) => f.rule);
  assert.deepEqual(rules, ["doc-check-waiver", "one-name-per-role"]);
});

test("a rule with problems never matches anything", () => {
  const rule = compile({ file: "x.md", id: "x", meta: { name: "x" }, fails: [], passes: [] });
  assert.ok(rule.problems.length > 0);
  assert.deepEqual(checkText("anything", [rule]), []);
});

test("the checker reads its own repo root", () => {
  assert.ok(fs.existsSync(path.join(ROOT, "scripts", "doc-rules")));
  assert.equal(parseRule(shipped[0].file).id, shipped[0].id);
});

test("a waiver that names no real rule is a finding", () => {
  const text = "<!-- doc-check-allow no-such-rule: because I said so, really -->\nfine";
  const [finding] = checkText(text, shipped, "doc.md");
  assert.equal(finding.rule, "doc-check-waiver");
  assert.match(finding.message, /does not exist: no-such-rule/);
});

test("a waiver shown inside a code span or a fence is only an example", () => {
  const example = "<!-- doc-check-allow one-name-per-role: quoting a product name -->";
  const text = ["Write `" + example + "` above the line.", "```", example, "```", "Grok Bot"].join("\n");
  assert.deepEqual(checkText(text, shipped, "doc.md").map((f) => f.line), [5]);
});
