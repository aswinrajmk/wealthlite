# AI evals: scoring the test cases the local model drafts

LLM output changes wording on every run, so these evals don't compare text. They check the properties a QA reviewer
checks by eye, and they explain every point taken off. Think of them as unit tests for the AI's answers.

| Check        | Points | What it catches                                                                               |
| ------------ | ------ | --------------------------------------------------------------------------------------------- |
| Coverage     | 50     | An acceptance criterion with no test case                                                     |
| Duplicates   | 15     | Two cases testing the same thing                                                              |
| Consistency  | 15     | A case that contradicts itself, e.g. "has signed out and is logged in" (the real KAN-6 draft) |
| Completeness | 10     | A missing criterion, preconditions or expected result, or fewer than 2 or more than 5 steps   |
| Size         | 10     | Fewer than 3 or more than 6 cases (the prompt asks for a minimal set)                         |

The real KAN-6 draft scores **90%**: full coverage, but cases 1 and 3 contradict themselves.

## Files

- `score.ts`: `scoreTestCases(story, cases)` is pure and deterministic, with no model calls. Its unit tests are in
  `score.test.ts`.
- `golden/`: 20 sample stories in WealthLite's domain. Each names the **trap** it probes: vague wording, two checks
  in one line, money formats, roles, no "Acceptance criteria" heading, too many criteria for a minimal set, and so
  on. `golden.test.ts` keeps the set well formed and checks the parser reads each story correctly.

```sh
npm run test:evals
```

## The CI gate (`.github/workflows/ai-evals.yml`)

When a pull request changes the intake prompt (`automation/prompts/intake.json`), the scorer, the golden set or
`build_intake.py`:

1. GitHub Actions installs Ollama and pulls the model.
2. `run.ts` sends every golden story through the same two calls the n8n workflow makes (analysis, then test cases,
   with a fixed seed).
3. Each draft is scored, and the results table appears in the run summary.
4. The job **fails if the average drops more than 5 points** below `baseline.json`.

In CI, the 20 stories run as **4 parallel jobs** of 5 stories each, one CPU runner per job. A `gate` job then
combines them, writes the table, applies the 5-point gate and records the result. It fails if any story is
missing, so a lost shard can't make it pass.

The prompt text lives only in `automation/prompts/intake.json`, so production and the eval can't drift apart.

```sh
node automation/evals/run.ts                       # compare with the baseline (what CI runs)
node automation/evals/run.ts --update-baseline     # accept the current result as the new baseline
EVAL_MODEL=qwen2.5-coder:7b node automation/evals/run.ts --no-gate   # try another model
```

To compare models in CI without gating, run **AI evals** from the Actions tab and type the model name.

## Where it's used (build order)

1. **Now:** the scorer and the golden set, with unit tests in PR checks.
2. **Done:** a CI gate. The intake prompt runs against Ollama on every golden story, and a pull request that changes
   the prompt or `LOCAL_MODEL` fails if the average score drops.
3. **Done:** the live score. n8n scores each real draft and shows it in the Telegram approval, e.g.
   `Score 90% · 3/3 criteria · 0 duplicates · ⚠ contradiction in case 1`.
4. **Then:** an AI quality section on the QA dashboard.
