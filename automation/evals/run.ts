// Runs the intake prompts (automation/prompts/intake.json) on every golden story against Ollama, scores the drafted
// test cases with score.ts, and compares the average with baseline.json. Exit code 1 when the average drops more
// than the tolerance: a prompt or model change made the drafts worse.
//
//   node automation/evals/run.ts                      run and compare (CI gate)
//   node automation/evals/run.ts --update-baseline    run and save the result as the new baseline
//   EVAL_MODEL=qwen2.5-coder:7b node automation/evals/run.ts --no-gate   compare another model, without gating
//
// In CI the golden set runs as 4 parallel jobs, then one job combines them and gates:
//   node automation/evals/run.ts --shard=2/4 --out=shard-2.json   evaluate stories 2, 6, 10, … and save them
//   node automation/evals/run.ts --combine=shards/                 load every shard file, then report and gate
//
// Env: OLLAMA_URL (default http://127.0.0.1:11434), EVAL_MODEL, EVAL_ONLY=G-01,G-02, REPORT (markdown output path).

import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { scoreTestCases, type Score, type Story, type TestCase } from "./score.ts";

interface Prompts {
  version: string;
  model: string;
  options: Record<string, unknown>;
  analysis: { system: string; schema: unknown };
  testCases: { system: string; schema: unknown };
}
interface Golden extends Story {
  traps: string[];
}
interface Baseline {
  model: string;
  promptVersion: string;
  mean: number;
  perStory: Record<string, number>;
}
interface StoryResult {
  key: string;
  score: number;
  summary: string;
  ms: number;
  error?: string;
}

const TOLERANCE = 5;
const SEED = 42; // fixed sampling seed so reruns on the same machine are comparable
const here = new URL("./", import.meta.url);
const prompts = JSON.parse(readFileSync(new URL("../prompts/intake.json", here), "utf8")) as Prompts;
const baselineUrl = new URL("baseline.json", here);
const argv = process.argv.slice(2);
const args = new Set(argv);
const option = (name: string) => argv.find((a) => a.startsWith(`--${name}=`))?.slice(name.length + 3);
const shard = option("shard"); // "2/4"
const out = option("out");
const combine = option("combine");
const ollama = (process.env.OLLAMA_URL ?? "http://127.0.0.1:11434").replace(/\/$/, "");
const model = process.env.EVAL_MODEL || prompts.model;
const only = new Set((process.env.EVAL_ONLY ?? "").split(",").filter(Boolean));

const golden = readdirSync(new URL("golden/", here))
  .filter((f) => f.endsWith(".json"))
  .sort()
  .map((f) => JSON.parse(readFileSync(new URL(`golden/${f}`, here), "utf8")) as Golden)
  .filter((g) => !only.size || only.has(g.key));

function inShard(index: number): boolean {
  if (!shard) return true;
  const [i, n] = shard.split("/").map(Number);
  if (!i || !n || i < 1 || i > n) throw new Error(`--shard must look like 2/4, got "${shard}"`);
  return index % n === i - 1;
}

/** One chat call with JSON-schema output, exactly as the n8n workflow sends it (plus a fixed seed). */
async function chat(system: string, schema: unknown, user: string): Promise<unknown> {
  const response = await fetch(`${ollama}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      stream: false,
      think: false,
      options: { ...prompts.options, seed: SEED },
      format: schema,
      messages: [
        { role: "system", content: system },
        { role: "user", content: user },
      ],
    }),
    signal: AbortSignal.timeout(10 * 60 * 1000),
  });
  if (!response.ok) throw new Error(`Ollama responded ${response.status}: ${(await response.text()).slice(0, 200)}`);
  const body = (await response.json()) as { message?: { content?: string } };
  return JSON.parse(body.message?.content ?? "");
}

async function evaluate(story: Golden): Promise<StoryResult> {
  const started = Date.now();
  try {
    const head = `Story ${story.key}: ${story.summary}\n\n${story.description}`;
    const analysis = await chat(prompts.analysis.system, prompts.analysis.schema, head);
    const drafted = (await chat(
      prompts.testCases.system,
      prompts.testCases.schema,
      `${head}\n\nApproved analysis:\n${JSON.stringify(analysis)}`,
    )) as { cases?: TestCase[] };
    // The workflow keeps at most 6 cases (Format test cases node); score what the reviewer would see.
    const s: Score = scoreTestCases(story, (drafted.cases ?? []).slice(0, 6));
    return { key: story.key, score: s.score, summary: s.summary, ms: Date.now() - started };
  } catch (err) {
    return { key: story.key, score: 0, summary: "no usable output", ms: Date.now() - started, error: String(err) };
  }
}

const results: StoryResult[] = [];
if (combine) {
  // Combine the shard files from the parallel jobs. Every golden story must be there exactly once: a lost or
  // failed shard must never make the gate pass on a partial set.
  for (const f of readdirSync(combine).filter((name) => name.endsWith(".json"))) {
    results.push(...(JSON.parse(readFileSync(join(combine, f), "utf8")) as StoryResult[]));
  }
  results.sort((a, b) => a.key.localeCompare(b.key));
  const want = golden.map((g) => g.key);
  const got = results.map((r) => r.key);
  const missing = want.filter((k) => !got.includes(k));
  if (missing.length || got.length !== new Set(got).size) {
    console.error(`FAIL: the shards do not cover the golden set once each. Missing: ${missing.join(", ") || "none"}.`);
    process.exit(1);
  }
} else {
  for (const [index, story] of golden.entries()) {
    if (!inShard(index)) continue;
    const r = await evaluate(story);
    results.push(r);
    console.log(
      `${r.key.padEnd(5)} ${String(r.score).padStart(3)}%  ${(r.ms / 1000).toFixed(0).padStart(4)} s  ${r.error ?? r.summary}`,
    );
  }
}

if (out) {
  // A shard only saves its results. Reporting, recording and the gate happen once, in the combine step.
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, JSON.stringify(results, null, 2) + "\n");
  console.log(`Saved ${results.length} results to ${out}.`);
  process.exit(0);
}
const mean = Math.round((results.reduce((sum, r) => sum + r.score, 0) / Math.max(results.length, 1)) * 10) / 10;

let baseline: Baseline | null = null;
try {
  baseline = JSON.parse(readFileSync(baselineUrl, "utf8")) as Baseline;
} catch {
  baseline = null;
}

const lines = [
  `## AI eval: intake test cases`,
  "",
  `Model \`${model}\` · prompt version \`${prompts.version}\` · ${results.length} golden stories · **average ${mean}%**` +
    (baseline ? ` (baseline ${baseline.mean}% with \`${baseline.model}\`, prompt \`${baseline.promptVersion}\`)` : ""),
  "",
  "| Story | Score | Baseline | Findings |",
  "| --- | ---: | ---: | --- |",
  ...results.map((r) => {
    const before = baseline?.perStory[r.key];
    const delta =
      before === undefined
        ? "–"
        : `${before}%${r.score !== before ? ` (${r.score > before ? "+" : ""}${r.score - before})` : ""}`;
    return `| ${r.key} | ${r.score}% | ${delta} | ${(r.error ?? r.summary).replace(/\|/g, "/")} |`;
  }),
];
const report = lines.join("\n") + "\n";
if (process.env.REPORT) writeFileSync(process.env.REPORT, report);
console.log(`\nAverage ${mean}% over ${results.length} stories (model ${model}, prompt ${prompts.version}).`);

// --record: send the result to the portfolio's AI quality section (only full runs, only when the token is set).
if (args.has("--record") && !only.size) {
  const token = process.env.AI_EVALS_TOKEN;
  const url = process.env.AI_EVALS_URL ?? "https://portfolio.aswin-qa.workers.dev/api/ai-evals";
  if (!token) {
    console.warn("AI_EVALS_TOKEN is not set, so the result was not recorded.");
  } else {
    const response = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: "golden",
        model,
        promptVersion: prompts.version,
        score: mean,
        details: { perStory: Object.fromEntries(results.map((r) => [r.key, r.score])) },
        runUrl: process.env.CI_RUN_URL || undefined,
      }),
      signal: AbortSignal.timeout(15_000),
    }).catch((err: unknown) => err);
    // Recording is a report, never a gate: a failure here only warns.
    if (response instanceof Response && response.ok) console.log("Recorded on the QA dashboard.");
    else
      console.warn(`Could not record the result: ${response instanceof Response ? response.status : String(response)}`);
  }
}

if (args.has("--update-baseline")) {
  const next: Baseline = {
    model,
    promptVersion: prompts.version,
    mean,
    perStory: Object.fromEntries(results.map((r) => [r.key, r.score])),
  };
  writeFileSync(baselineUrl, JSON.stringify(next, null, 2) + "\n");
  console.log(`Saved the baseline: ${mean}%.`);
} else if (!args.has("--no-gate") && baseline && !only.size) {
  if (mean < baseline.mean - TOLERANCE) {
    console.error(`FAIL: average ${mean}% is more than ${TOLERANCE} points below the baseline ${baseline.mean}%.`);
    process.exit(1);
  }
  console.log(`PASS: within ${TOLERANCE} points of the baseline ${baseline.mean}%.`);
}
