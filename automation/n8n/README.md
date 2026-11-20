# Jira story → approved test cases (n8n)

A local n8n workflow that turns new stories on the WealthLite Jira board (project `KAN`) into
Jira **Test** issues. You approve each step in Telegram.

```
Every minute → new KAN Story? → Qwen (Ollama) analysis → Telegram ✅/❌
  → analysis posted as a Jira comment → Qwen drafts 3–6 minimal test cases → Telegram ✅/❌
  → one "Test" issue per case, linked "is tested by" to the story → Telegram summary
```

| File                     | Purpose                                                                   |
| ------------------------ | ------------------------------------------------------------------------- |
| `jira-story-intake.json` | The workflow, ready to import. Credentials and chat id are placeholders.  |
| `build_intake.py`        | Generates the workflow, pushes it to the local n8n and rewrites the JSON. |

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
