import { beforeAll, describe, expect, it } from "vitest";
import rawFixtures from "./parity/fixtures.json?raw";
import { createWorker } from "../src/index";
import { API, FakeGitHub, expand, type Dataset } from "./helpers/fake-github";
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
});
