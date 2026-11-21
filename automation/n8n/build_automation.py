"""Build the 'Jira Test Automation' n8n workflow: Test issues labelled `smoke` or `regression` → Telegram
approval → repository_dispatch to qa-tests (automate-from-jira.yml), which turns them into Playwright tests.

The approval has three buttons, so the owner picks who writes the code:
  ✅ Claude  — qa-tests runs Claude Code in GitHub Actions (needs a Claude subscription token there).
  🏠 Local   — the local model (Ollama, LOCAL_MODEL) drafts the spec here on the Mac, from the story's test
               cases and the framework's own files read from GitHub; qa-tests then checks it and opens the PR.
  ❌ Not now — the tests are labelled auto-rejected.

Usage (from the repo root):
    set -a; source ~/.config/wealthlite/jira.env; set +a
    python3 -I automation/n8n/build_automation.py automation/n8n/jira-test-automation.json [--push]

Without --push it only writes the secret-free JSON. --push needs JIRA_SITE, TELEGRAM_CHAT_ID, N8N_API_KEY and
QA_DISPATCH_TOKEN (fine-grained PAT, qa-tests only, Contents: read and write). The first push creates the
workflow (inactive) and its GitHub credential and prints their ids; put them in WF_ID / GITHUB_CRED below.

Label states on Test issues (see README):
  smoke|regression → auto-pending (waiting for YES) → auto-queued (sent to qa-tests) → auto-pr-open → automated
  rejected or no answer in 24h → auto-rejected (remove it to ask again); a failed run → auto-failed."""
import json, os, sys, urllib.error, urllib.request

SITE = "https://" + os.environ.get("JIRA_SITE", "<JIRA_SITE>").removeprefix("https://")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "<TELEGRAM_CHAT_ID>")
JIRA_CRED = {"id": "VD72DSMts0FXFVUi", "name": "Jira WealthLite"}
TG_CRED = {"id": "mrivhYzNiVsgPznQ", "name": "Telegram aswinqabot"}
GITHUB_CRED = {"id": "3JhDdYxTwbKPvsB2", "name": "GitHub qa-tests dispatch"}
WF_ID = "Pndy05uJMPxEXlQ9"
REPO_OUT = sys.argv[1]
PUSH = "--push" in sys.argv

LOCAL_MODEL = "qwen3.5:2b"
# Framework files the local model sees, so it reuses fixtures and page objects instead of inventing selectors.
CONTEXT_FILES = [
    "docs/CONVENTIONS.md",
    "src/fixtures/index.ts",
    "src/fixtures/pages.fixture.ts",
    "src/fixtures/auth.fixture.ts",
    "src/pages/LoginPage.ts",
    "src/pages/PortfolioPage.ts",
    "src/components/AppHeader.ts",
    "src/components/HoldingsTable.ts",
    "src/data/users.ts",
    "tests/ui/auth/kan-6-sign-out.spec.ts",
]

STATE_LABELS = "auto-pending, auto-queued, auto-pr-open, automated, auto-failed, auto-rejected"
JQL = (f"project = KAN AND issuetype = Test AND labels in (smoke, regression) AND labels not in ({STATE_LABELS}) "
       "ORDER BY created ASC")

GROUP = r"""
// Group the labelled Test issues by their story (Test --Tests--> Story) and take the oldest story only:
// one story per execution, because each run pauses on its own approval link.
function adfText(node) {
  if (!node) return '';
  if (node.type === 'text') return node.text || '';
  if (node.type === 'hardBreak') return '\n';
  const inner = (node.content || []).map(adfText).join('');
  if (node.type === 'listItem') return '- ' + inner;
  if (['paragraph', 'heading', 'codeBlock', 'blockquote'].includes(node.type)) return inner + '\n';
  return inner;
}
// Folder under tests/ui for a local draft, guessed from the story title (Claude Code picks its own).
function featureOf(title) {
  const t = title.toLowerCase();
  if (/sign|log ?in|log ?out|password|auth/.test(t)) return 'auth';
  if (/holding|portfolio|total|value/.test(t)) return 'portfolio';
  return 'stories';
}
const byStory = new Map();
for (const issue of $input.first().json.issues || []) {
  const link = (issue.fields.issuelinks || []).find((l) => l.type.name === 'Tests' && l.outwardIssue);
  if (!link) continue; // a Test with no story cannot be traced, so it is skipped
  const story = link.outwardIssue;
  const entry = byStory.get(story.key) ||
    { story: story.key, title: story.fields.summary, feature: featureOf(story.fields.summary), tests: [] };
  const suite = issue.fields.labels.includes('smoke') ? 'smoke' : 'regression';
  entry.tests.push({ key: issue.key, suite, title: issue.fields.summary.replace(/^\[KAN-\d+\]\s*/, ''),
    description: adfText(issue.fields.description).trim() });
  byStory.set(story.key, entry);
}
const first = [...byStory.values()][0];
if (!first) return [];
const lines = first.tests.map((t) => `• ${t.key} (${t.suite}) ${t.title}`);
const text = [`🤖 Automate ${first.tests.length} test(s) for ${first.story}: ${first.title}`, '', ...lines,
  '', `${'SITE'}/browse/${first.story}`].join('\n').slice(0, 3800);
return [{ json: { ...first, keys: first.tests.map((t) => t.key).join(','), text } }];
""".replace("'SITE'", json.dumps(SITE))

SPLIT = r"""
// One item per Test issue, so the next HTTP node relabels each of them.
return $('Group by story').first().json.tests.map((t) => ({ json: t }));
"""
CONTEXT_ITEMS = r"""
// One item per framework file to read from GitHub.
return CONTEXT.map((path) => ({ json: { path } }));
""".replace("CONTEXT", json.dumps(CONTEXT_FILES))

BUILD_PROMPT = r"""
// A 2B model copies examples and drifts from instructions, so the structure is not left to it: this node writes
// the skeleton (describe, test titles, exact tags, one test.step per Jira step) and the model only fills the bodies.
const g = $('Group by story').first().json;
const paths = $('Context files').all().map((i) => i.json.path);
const EXAMPLE = 'tests/ui/auth/kan-6-sign-out.spec.ts';
const files = $input.all().map((item, i) => {
  const note = paths[i] === EXAMPLE ? ' (EXAMPLE ONLY: already automated, do NOT copy its tests, titles or tags)' : '';
  return `--- FILE ${paths[i]}${note} ---\n${item.json.data || ''}`;
}).join('\n\n');
const q = (t) => JSON.stringify(String(t).replace(/\s+/g, ' ').trim());
// Steps and the expected result from the Test issue description ("Steps" list, "Expected result" section).
function parse(desc) {
  const lines = (desc || '').split('\n').map((l) => l.trim()).filter(Boolean);
  const steps = []; let expected = ''; let mode = '';
  for (const l of lines) {
    if (/^steps$/i.test(l)) { mode = 'steps'; continue; }
    if (/^expected result$/i.test(l)) { mode = 'expected'; continue; }
    if (/^preconditions$/i.test(l)) { mode = 'pre'; continue; }
    if (mode === 'steps') steps.push(l.replace(/^[-\d.)\s]+/, ''));
    else if (mode === 'expected') expected += (expected ? ' ' : '') + l;
  }
  return { steps: steps.length ? steps : ['Do the action under test'], expected: expected || 'See the test title' };
}
const tests = g.tests.map((t) => {
  const { steps, expected } = parse(t.description);
  const body = [
    ...steps.map((st) => `      await test.step(${q(st)}, async () => {\n        // TODO\n      });`),
    `      await test.step(${q('Expect: ' + expected)}, async () => {\n        // TODO: await expect(...)\n      });`,
  ].join('\n\n');
  return `  test(${q(t.title)}, { tag: [${q('@' + t.key)}, ${q('@' + t.suite)}] }, async ({ signedIn, loginPage, portfolioPage }) => {\n${body}\n  });`;
}).join('\n\n');
const skeleton = `import { expect, test } from "../../../src/fixtures/index.ts";\n\n` +
  `test.describe(${q(g.title)}, { tag: ${q('@' + g.story)} }, () => {\n${tests}\n});\n`;
const system = [
  'You are a senior QA automation engineer completing a Playwright + TypeScript spec for the qa-tests repo.',
  'You get a SKELETON. Return the same file with every TODO replaced by real code. Rules:',
  '- Keep the import, the describe, every test title and every tag EXACTLY as in the skeleton. Add no other tests.',
  '- Use only the fixtures (signedIn, loginPage, portfolioPage) and the methods shown in the repository files.',
  '  Remove fixtures a test does not use from its parameter list.',
  '- Never write CSS or XPath selectors. Never use waitForTimeout, test.only or test.skip.',
  '- Assertions use web-first expect, e.g. await expect(loginPage.page).toHaveURL(/\\/login$/).',
  '- Reply with the complete file in a single ```ts code block and nothing else.',
].join('\n');
const user = [
  `Repository files for reference:\n\n${files}`,
  `Story ${g.story}: ${g.title}`,
  `SKELETON to complete:\n\n\`\`\`ts\n${skeleton}\`\`\``,
].join('\n\n');
return [{ json: { skeleton, body: {
  model: MODEL, stream: false, think: false,
  options: { temperature: 0.1, num_ctx: 16384 },
  messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
} } }];
""".replace("MODEL", json.dumps(LOCAL_MODEL))

LOCAL_PAYLOAD = r"""
// The model's reply goes to qa-tests as is; qa-tests extracts the code and checks it. An empty or failed reply
// still goes, so the run fails there and the Jira tests are labelled auto-failed with a link to the log.
const g = $('Group by story').first().json;
let spec = ($json.message && $json.message.content) || '';
// No usable reply (timeout, Ollama down, or no test in it): send the skeleton instead, so the owner still gets a
// draft PR with the structure and tags right, to finish by hand.
if (!/\btest(\.describe)?\s*\(/.test(spec)) spec = $('Build prompt').first().json.skeleton;
return [{ json: { payload: { event_type: 'jira-tests-labelled', client_payload: {
  story: g.story, engine: 'local', model: MODEL, feature: g.feature, spec: spec.slice(0, 30000),
} } } }];
""".replace("MODEL", json.dumps(LOCAL_MODEL))

BACK_TO_ONE = r"""
// The relabelling ran once per test; continue once per story.
return [{ json: $('Group by story').first().json }];
"""


class _Resume:
    """Approval link for the waiting execution. Telegram rejects 'localhost' button URLs, so use 127.0.0.1."""
    @staticmethod
    def format(d):
        return ("={{ ((u) => u + (u.includes('?') ? '&' : '?') + 'd=" + d + "&s=automate')"
                "($execution.resumeUrl.replace('//localhost', '//127.0.0.1')) }}")


def node(name, type_, version, pos, params, creds=None):
    n = {"name": name, "type": type_, "typeVersion": version, "position": pos, "parameters": params}
    if creds:
        n["credentials"] = creds
    return n


def chose(engine):
    """IF params: true when the click was this button for this approval."""
    return {"conditions": {"string": [
        {"value1": "={{ $json.query ? $json.query.d : 'timeout' }}", "operation": "equal", "value2": engine},
        {"value1": "={{ $json.query ? $json.query.s : '' }}", "operation": "equal", "value2": "automate"},
    ]}, "combineOperation": "all"}


def dispatch(name, pos, body):
    return node(name, "n8n-nodes-base.httpRequest", 4.2, pos, {
        "method": "POST",
        "url": "https://api.github.com/repos/aswinrajmk/qa-tests/dispatches",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Accept", "value": "application/vnd.github+json"}]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": body,
        "options": {},
    }, {"httpHeaderAuth": GITHUB_CRED})


def relabel(name, pos, remove, add):
    params = {
        "method": "PUT",
        "url": f"={SITE}/rest/api/3/issue/{{{{ $json.key }}}}",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "jiraSoftwareCloudApi",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": json.dumps({"update": {"labels": ([{"remove": remove}] if remove else []) + [{"add": add}]}}),
        "options": {},
    }
    return node(name, "n8n-nodes-base.httpRequest", 4.2, pos, params, {"jiraSoftwareCloudApi": JIRA_CRED})


def telegram(name, pos, text, buttons=None):
    params = {"chatId": CHAT_ID, "text": text, "additionalFields": {"appendAttribution": False}}
    if buttons:
        params.update({"replyMarkup": "inlineKeyboard", "inlineKeyboard": buttons})
    return node(name, "n8n-nodes-base.telegram", 1.2, pos, params, {"telegramApi": TG_CRED})


code = lambda name, pos, js: node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})

nodes = [
    node("Every minute", "n8n-nodes-base.scheduleTrigger", 1.2, [0, 0],
         {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}}),
    node("Search labelled tests", "n8n-nodes-base.httpRequest", 4.2, [220, 0], {
        "url": f"{SITE}/rest/api/3/search/jql",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "jiraSoftwareCloudApi",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "jql", "value": JQL},
            {"name": "fields", "value": "summary,description,labels,issuelinks"},
            {"name": "maxResults", "value": "50"},
        ]},
        "options": {},
    }, {"jiraSoftwareCloudApi": JIRA_CRED}),
    code("Group by story", [440, 0], GROUP),
    code("Split tests (claim)", [660, 0], SPLIT),
    relabel("Label auto-pending", [880, 0], None, "auto-pending"),
    code("One per story (ask)", [1100, 0], BACK_TO_ONE),
    telegram("Ask automation approval", [1320, 0],
             "={{ $json.text }}\n\nWho should write the Playwright tests?\n"
             "✅ Claude: Claude Code in GitHub Actions (needs your Claude subscription)\n"
             f"🏠 Local: {LOCAL_MODEL} on this Mac, one draft, review it closely",
             {"rows": [{"row": {"buttons": [
                 {"text": "✅ Claude", "additionalFields": {"url": _Resume.format("claude")}},
                 {"text": "🏠 Local", "additionalFields": {"url": _Resume.format("local")}},
                 {"text": "❌ Not now", "additionalFields": {"url": _Resume.format("reject")}},
             ]}}]}),
    node("Wait for decision", "n8n-nodes-base.wait", 1.1, [1540, 0],
         {"resume": "webhook", "httpMethod": "GET", "limitWaitTime": True, "limitType": "afterTimeInterval",
          "resumeAmount": 24, "resumeUnit": "hours", "options": {}}),
    node("Claude?", "n8n-nodes-base.if", 1, [1760, 0], chose("claude")),
    node("Local?", "n8n-nodes-base.if", 1, [1980, 160], chose("local")),
    # Claude: qa-tests runs Claude Code
    code("Split tests (Claude)", [1980, -320], SPLIT),
    relabel("Label auto-queued (Claude)", [2200, -320], "auto-pending", "auto-queued"),
    code("One per story (Claude)", [2420, -320], BACK_TO_ONE),
    dispatch("Dispatch to qa-tests (Claude)", [2640, -320],
             "={{ JSON.stringify({ event_type: 'jira-tests-labelled', "
             "client_payload: { story: $json.story, engine: 'claude' } }) }}"),
    telegram("Confirm Claude", [2860, -320],
             "=🚀 {{ $('Group by story').first().json.story }}: Claude Code is writing the tests "
             "({{ $('Group by story').first().json.keys }}). You'll get the PR here."),
    # Local: draft the spec here with Ollama, then send it to qa-tests
    code("Split tests (local)", [2200, 0], SPLIT),
    relabel("Label auto-queued (local)", [2420, 0], "auto-pending", "auto-queued"),
    code("One per story (local)", [2640, 0], BACK_TO_ONE),
    telegram("Tell drafting locally", [2860, 0],
             f"=🏠 {{{{ $('Group by story').first().json.story }}}}: {LOCAL_MODEL} is drafting the spec on this Mac. "
             "This takes a few minutes."),
    code("Context files", [3080, 0], CONTEXT_ITEMS),
    node("Read file from GitHub", "n8n-nodes-base.httpRequest", 4.2, [3300, 0], {
        "url": "=https://api.github.com/repos/aswinrajmk/qa-tests/contents/{{ $json.path }}",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Accept", "value": "application/vnd.github.raw"}]},
        "options": {"response": {"response": {"responseFormat": "text", "outputPropertyName": "data"}}},
    }, {"httpHeaderAuth": GITHUB_CRED}),
    code("Build prompt", [3520, 0], BUILD_PROMPT),
    {**node("Local model writes spec", "n8n-nodes-base.httpRequest", 4.2, [3740, 0], {
        "method": "POST",
        "url": "http://127.0.0.1:11434/api/chat",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json.body) }}",
        "options": {"timeout": 900000},
    }), "onError": "continueRegularOutput"},
    code("Prepare local dispatch", [3960, 0], LOCAL_PAYLOAD),
    dispatch("Dispatch to qa-tests (local)", [4180, 0], "={{ JSON.stringify($json.payload) }}"),
    telegram("Confirm local", [4400, 0],
             "=📨 {{ $('Group by story').first().json.story }}: local draft sent to qa-tests for checks. "
             "You'll get the PR here (a draft PR if the checks fail)."),
    # Rejected or no answer
    code("Split tests (reject)", [2200, 320], SPLIT),
    relabel("Label auto-rejected", [2420, 320], "auto-pending", "auto-rejected"),
    code("One per story (reject)", [2640, 320], BACK_TO_ONE),
    telegram("Confirm not automated", [2860, 320],
             "=⏸ {{ $json.story }}: not automated. Remove auto-rejected from a Test issue to ask again."),
]

connections = {}


def chain(*names):
    for a, b in zip(names, names[1:]):
        connections[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}


def branch(name, yes, no):
    connections[name] = {"main": [[{"node": yes, "type": "main", "index": 0}],
                                  [{"node": no, "type": "main", "index": 0}]]}


chain("Every minute", "Search labelled tests", "Group by story", "Split tests (claim)", "Label auto-pending",
      "One per story (ask)", "Ask automation approval", "Wait for decision", "Claude?")
branch("Claude?", "Split tests (Claude)", "Local?")
branch("Local?", "Split tests (local)", "Split tests (reject)")
chain("Split tests (Claude)", "Label auto-queued (Claude)", "One per story (Claude)",
      "Dispatch to qa-tests (Claude)", "Confirm Claude")
chain("Split tests (local)", "Label auto-queued (local)", "One per story (local)", "Tell drafting locally",
      "Context files", "Read file from GitHub", "Build prompt", "Local model writes spec",
      "Prepare local dispatch", "Dispatch to qa-tests (local)", "Confirm local")
chain("Split tests (reject)", "Label auto-rejected", "One per story (reject)", "Confirm not automated")

wf = {"name": "Jira Test Automation", "nodes": nodes, "connections": connections,
      "settings": {"executionOrder": "v1"}}


def call(method, path, body=None):
    req = urllib.request.Request(f"http://localhost:5678/api/v1{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-N8N-API-KEY": os.environ["N8N_API_KEY"],
                                          "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} failed: {e.code} {e.read().decode()[:500]}")


if PUSH:
    if not GITHUB_CRED["id"]:
        cred = call("POST", "/credentials", {"name": GITHUB_CRED["name"], "type": "httpHeaderAuth", "data": {
            "name": "Authorization", "value": "Bearer " + os.environ["QA_DISPATCH_TOKEN"],
            # The token can only be sent to GitHub's API, whatever a node is edited to call.
            "allowedHttpRequestDomains": "domains", "allowedDomains": "api.github.com"}})
        GITHUB_CRED["id"] = cred["id"]
        print("created credential:", cred["id"], "(put it in GITHUB_CRED)")
    if WF_ID:
        saved = call("PUT", f"/workflows/{WF_ID}", wf)
    else:
        saved = call("POST", "/workflows", wf)
        print("created workflow:", saved["id"], "(put it in WF_ID; activate it in n8n when qa-tests is merged)")
    print("workflow saved:", saved["id"], "active:", saved.get("active"), "nodes:", len(saved["nodes"]))

# Repo copy: no chat id, no credential ids.
repo = json.loads(json.dumps(wf))
for n in repo["nodes"]:
    if n["type"] == "n8n-nodes-base.telegram":
        n["parameters"]["chatId"] = "<TELEGRAM_CHAT_ID>"
    for c in n.get("credentials", {}).values():
        c["id"] = "<set-in-n8n>"
with open(REPO_OUT, "w") as f:
    json.dump(repo, f, indent=2)
    f.write("\n")
print("wrote", REPO_OUT)
