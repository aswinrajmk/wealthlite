# Jira story → approved test cases → automated tests (n8n)

A local n8n workflow that turns new stories on the WealthLite Jira board (project `KAN`) into
Jira **Test** issues. You approve each step in Telegram.

```
Every minute → new KAN Story? → Qwen (Ollama) analysis → Telegram ✅/❌
  → analysis posted as a Jira comment → Qwen drafts 3–6 minimal test cases → Telegram ✅/❌
  → one "Test" issue per case, linked "is tested by" to the story → Telegram summary
```

| File                        | Purpose                                                                                 |
| --------------------------- | --------------------------------------------------------------------------------------- |
| `jira-story-intake.json`    | Story → test cases workflow, ready to import. Credentials and chat id are placeholders. |
| `build_intake.py`           | Generates the intake workflow, pushes it to the local n8n and rewrites the JSON.        |
| `jira-test-automation.json` | Suite label → automated tests workflow (see below).                                     |
| `build_automation.py`       | Generates the automation workflow. With `--push` it also creates it in n8n.             |

## Writing a story

- Work type **Story** in project **KAN**. Write the whole description in the Create dialog,
  because a story is only analysed the first time it is seen.
- Description: `As a … I want … so that …`, then `Acceptance criteria:` with one
  `Given … when … then …` per line. Synthetic data only.
- Don't add `ai-*` labels yourself. Other labels are fine.

## Labels (the workflow's state)

| Label                  | Meaning                                                           |
| ---------------------- | ----------------------------------------------------------------- |
| `ai-new`               | Picked up, waiting for the analysis approval                      |
| `ai-analysis-approved` | Analysis approved, waiting for the test-case approval             |
| `ai-done`              | Test issues created                                               |
| `ai-rejected`          | Rejected, or no answer within 24h. Remove the label to run again. |

Created tests get `ai-generated` plus `tc-positive`, `tc-negative` or `tc-edge`.

## Automating labelled tests ("Jira Test Automation")

Not every manual test case is worth automating. Pick the ones that are by adding a label to the **Test** issue:
`smoke` (critical path, runs on every deploy) or `regression` (nightly).

```
Every minute → Test issues labelled smoke/regression, no auto-* label → group by story (oldest story first)
  → label auto-pending → Telegram ✅ Automate / ❌ Not now
  → ✅ label auto-queued → repository_dispatch "jira-tests-labelled" {story} to qa-tests → Telegram
  → ❌ or no answer in 24h → label auto-rejected
```

qa-tests then writes the specs (`automate-from-jira.yml`), opens PR `auto/<story>`, and moves the labels on:

| Label           | Meaning                                                                  |
| --------------- | ------------------------------------------------------------------------ |
| `auto-pending`  | Waiting for your answer in Telegram                                      |
| `auto-queued`   | Sent to qa-tests                                                         |
| `auto-pr-open`  | PR open, linked in a comment on the story                                |
| `automated`     | PR merged (or the tests already matched)                                 |
| `auto-failed`   | The qa-tests run failed. Remove the label to retry                       |
| `auto-rejected` | You said not now, or didn't answer in 24h. Remove the label to ask again |

All of a story's labelled tests go into one spec and one PR. Labelling another test of the same story later
updates the open PR, or opens a new one.

Setup: add `QA_DISPATCH_TOKEN` to `~/.config/wealthlite/jira.env`. It is the same kind of fine-grained PAT as
the wealthlite secret of that name, with **Contents: read and write** on `aswinrajmk/qa-tests`, which
repository_dispatch needs. Then run
`python3 -I automation/n8n/build_automation.py automation/n8n/jira-test-automation.json --push`, put the
printed ids into the script, and activate the workflow in n8n. qa-tests needs the secrets `JIRA_SITE`,
`JIRA_EMAIL` and `JIRA_TOKEN`.

## Setup

1. Jira: add a **Test** work type to KAN, and an issue link type **Tests** (`tests` / `is tested by`).
2. Ollama running with `qwen3.5:2b`. The workflow calls `http://127.0.0.1:11434`, because Ollama
   listens on IPv4 only.
3. n8n at `http://localhost:5678` with credentials **Jira WealthLite** (Jira Software Cloud API) and
   **Telegram aswinqabot** (Telegram API).
4. Secrets live outside the repo in `~/.config/wealthlite/jira.env` (`JIRA_SITE`, `JIRA_EMAIL`,
   `JIRA_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `N8N_API_KEY`).
5. Import the JSON, set the chat id and the credentials, then activate it. Or run `build_intake.py`
   (see its docstring).

## Known limits

- Approve/Reject buttons open `127.0.0.1:5678`, so they only work from Telegram on the Mac running n8n.
  Telegram rejects `localhost` button URLs, so the workflow rewrites them.
- n8n only runs while `npx n8n` is running.
- Rejecting test cases is all or nothing. There's no revise step yet.
