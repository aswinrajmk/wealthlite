import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { parseCriteria } from "./score.ts";

// The golden set is test data for the model, so it gets its own checks: well formed, unique, and parsed the way
// a reviewer reads it. A story added with a criterion the parser misses would silently lower every score.
interface Golden {
  key: string;
  summary: string;
  description: string;
  expectedCriteria: number;
  traps: string[];
}

const dir = new URL("./golden/", import.meta.url);
const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
const stories = files.map((f) => ({ file: f, story: JSON.parse(readFileSync(new URL(f, dir), "utf8")) as Golden }));

test("has at least 20 stories with unique keys that match their file names", () => {
  assert.ok(stories.length >= 20, `only ${stories.length} golden stories`);
  assert.equal(new Set(stories.map((s) => s.story.key)).size, stories.length);
  for (const { file, story } of stories) assert.equal(`${story.key}.json`, file);
});

test("every story is complete and names the trap it tests", () => {
  for (const { story } of stories) {
    assert.ok(story.summary.trim(), `${story.key}: no summary`);
    assert.match(story.description, /^As (a|an|the) /, `${story.key}: description is not a user story`);
    assert.ok(story.traps.length > 0, `${story.key}: no trap named`);
  }
});

test("the parser finds exactly the expected acceptance criteria in every story", () => {
  for (const { story } of stories) {
    assert.equal(parseCriteria(story.description).length, story.expectedCriteria, story.key);
  }
});
