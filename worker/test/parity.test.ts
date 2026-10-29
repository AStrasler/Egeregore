import { beforeAll, describe, expect, it } from "vitest";
import rawFixtures from "./parity/fixtures.json?raw";
import { createWorker } from "../src/index";
import { API, FakeGitHub, expand, type Call, type Dataset, type FakeOptions } from "./helpers/fake-github";
import { testKey } from "./helpers/keys";
import { agentFromHeader, client } from "./helpers/rpc";

/**
 * Every case was recorded from the Python MCP server over the same fake data.
 * The Worker must return the same envelope, and the same text, for each one.
 */

// Parsed from text on purpose. Importing the JSON as a module turns it into an
// object literal, where a "__proto__" key would set the prototype and vanish.
const fixtures = JSON.parse(rawFixtures) as {
  now: string;
  dataset: unknown;
  cases: Array<{ name: string; tool: string }>;
  stories: Array<{ name: string; dataset: unknown; steps: Step[] }>;
};

interface Step {
  tool: string;
  args: Record<string, unknown>;
  agent: string;
  expected: unknown;
  text: string;
  text_exact: boolean;
  calls: Array<Record<string, unknown>>;
  fault?: { op: string; status: number; text: string; times: number; skip: number };
  drift?: { op: "list_issues"; number: number; set: Record<string, unknown> };
}

/** Where each GitHub operation the Python store can fail shows up on the wire. */
const FAULTS: Record<string, { method: string; match: RegExp }> = {
  create_comment: { method: "POST", match: /\/issues\/\d+\/comments$/ },
  create_issue: { method: "POST", match: /\/issues$/ },
  add_labels: { method: "POST", match: /\/labels$/ },
  remove_label: { method: "DELETE", match: /\/labels\// },
  list_comments: { method: "GET", match: /\/issues\/\d+\/comments$/ },
  get_issue: { method: "GET", match: /\/issues\/\d+$/ },
  get_pull: { method: "GET", match: /\/pulls\/\d+$/ },
};

const NOW = Date.parse(fixtures.now);
const dataset = expand(fixtures.dataset) as unknown as Dataset;

interface Case {
  name: string;
  tool: string;
  args: Record<string, unknown>;
  agent: string;
  expected: unknown;
  text: string;
  text_exact: boolean;
}

describe("parity with the Python MCP server", () => {
  let key: Awaited<ReturnType<typeof testKey>>;
  beforeAll(async () => {
    key = await testKey();
  });

  it("has recorded cases", () => {
    expect(fixtures.cases.length).toBeGreaterThan(40);
    const tools = new Set(fixtures.cases.map((item) => item.tool));
    expect([...tools].sort()).toEqual([
      "get_owner_decisions",
      "get_specialists",
      "get_task",
      "get_task_history",
      "get_tasks",
    ]);
  });

  it("answers every case through the whole Worker exactly as Python did", async () => {
    const github = new FakeGitHub({ dataset, now: () => NOW, publicKey: key.publicKey });
    const worker = createWorker({ fetch: github.fetch, now: () => NOW, resolveAgent: agentFromHeader, api: API });
    const env = { EGREGORE_APP_ID: "123", EGREGORE_APP_INSTALLATION_ID: "42", EGREGORE_APP_PRIVATE_KEY: key.pkcs8Pem };
    const mismatches: string[] = [];
    let busiest = 0;
    for (const item of fixtures.cases as unknown as Case[]) {
      const before = github.calls.length;
      const result = await client(worker, env, item.agent).tool(item.tool, item.args);
      busiest = Math.max(busiest, github.calls.length - before);
      try {
        expect(result.structuredContent, item.name).toEqual(item.expected);
        const text = result.content[0].text as string;
        if (item.text_exact) {
          expect(text, item.name).toBe(item.text);
        } else {
          // JavaScript orders integer-like keys first, so only the parsed value is comparable.
          expect(JSON.parse(text), item.name).toEqual(JSON.parse(item.text));
        }
        expect(result.isError ?? false, item.name).toBe(false);
      } catch (error) {
        mismatches.push(`${item.name}: ${(error as Error).message.slice(0, 6000)}`);
      }
    }
    expect(mismatches).toEqual([]);
    // No recorded case needs more than the 45-subrequest budget (one more is the first mint).
    expect(busiest).toBeLessThanOrEqual(46);
    // Only one token was ever minted: the isolate cache served the rest.
    expect(github.mints).toBe(1);
  });

  it("records the six write tools and every story step runs through the Worker", () => {
    const tools = new Set(fixtures.stories.flatMap((story) => story.steps.map((item) => item.tool)));
    for (const name of ["claim_task", "renew_lease", "release_task", "post_progress", "handoff_task", "request_review"]) {
      expect(tools.has(name), name).toBe(true);
    }
    expect(fixtures.stories.reduce((sum, story) => sum + story.steps.length, 0)).toBeGreaterThan(140);
  });

  for (const story of fixtures.stories) {
    it(`replays the "${story.name}" story: same answers, same GitHub calls, same writes`, async () => {
      const options: FakeOptions = {
        dataset: expand(structuredClone(story.dataset)) as unknown as Dataset,
        now: () => NOW,
        publicKey: key.publicKey,
      };
      const github = new FakeGitHub(options);
      const worker = createWorker({ fetch: github.fetch, now: () => NOW, resolveAgent: agentFromHeader, api: API });
      const env = { EGREGORE_APP_ID: "123", EGREGORE_APP_INSTALLATION_ID: "42", EGREGORE_APP_PRIVATE_KEY: key.pkcs8Pem };
      const mismatches: string[] = [];
      let index = 0;
      for (const item of story.steps) {
        index += 1;
        const label = `${story.name} #${index} ${item.tool} ${JSON.stringify(item.args).slice(0, 80)}`;
        if (item.fault) {
          const spec = FAULTS[item.fault.op];
          expect(spec, item.fault.op).toBeDefined();
          options.failWith = {
            status: item.fault.status,
            times: item.fault.times,
            skip: item.fault.skip,
            text: expand(item.fault.text),
            method: spec.method,
            match: spec.match,
          };
        } else {
          options.failWith = undefined;
        }
        options.drift = item.drift ? structuredClone(item.drift) : undefined;
        const before = github.calls.length;
        const result = await client(worker, env, item.agent).tool(item.tool, expand(item.args));
        const made = github.calls.slice(before);
        expect(made.length, label).toBeLessThanOrEqual(46);
        try {
          expect(result.structuredContent, label).toEqual(item.expected);
          const text = result.content[0].text as string;
          if (item.text_exact) expect(text, label).toBe(item.text);
          else expect(JSON.parse(text), label).toEqual(JSON.parse(item.text));
          expect(result.isError ?? false, label).toBe(false);
          // The same GitHub calls in the same order. A write the Python server did not make is a failure.
          const ops = new FakeGitHub({ dataset: options.dataset });
          (ops.calls as Call[]).push(...made);
          expect(ops.ops, label).toEqual(expand(item.calls));
        } catch (error) {
          mismatches.push(`${label}: ${(error as Error).message.slice(0, 4000)}`);
        }
      }
      expect(mismatches).toEqual([]);
    });
  }
});
