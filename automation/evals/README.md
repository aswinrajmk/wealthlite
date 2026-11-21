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

## Where it's used (build order)

1. **Now:** the scorer and the golden set, with unit tests in PR checks.
2. **Next:** a CI gate. The intake prompt runs against Ollama on every golden story, and a pull request that changes
   the prompt or `LOCAL_MODEL` fails if the average score drops.
3. **Then:** the live score. n8n scores each real draft and shows it in the Telegram approval, e.g.
   `Score 90% · 3/3 criteria · 0 duplicates · ⚠ contradiction in case 1`.
4. **Then:** an AI quality section on the QA dashboard.
