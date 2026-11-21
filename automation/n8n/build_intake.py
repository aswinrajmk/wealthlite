"""Build the 'Jira Story Intake' n8n workflow, push it to the local n8n via its API (updating
workflow WF_ID), and write a secret-free copy to the path given as the first argument.

Usage (from the repo root):
    set -a; source ~/.config/wealthlite/jira.env; set +a
    python3 -I automation/n8n/build_intake.py automation/n8n/jira-story-intake.json

Needs JIRA_SITE, TELEGRAM_CHAT_ID and N8N_API_KEY in the environment. The credential and
workflow ids below are references to objects in the local n8n, not secrets."""
import json, os, subprocess, sys, urllib.request

SITE = "https://" + os.environ["JIRA_SITE"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
API_KEY = os.environ["N8N_API_KEY"]
JIRA_CRED = {"id": "VD72DSMts0FXFVUi", "name": "Jira WealthLite"}
TG_CRED = {"id": "mrivhYzNiVsgPznQ", "name": "Telegram aswinqabot"}
# Header auth "Bearer <AI_EVALS_TOKEN>" for the portfolio's POST /api/ai-evals. With an empty id the "Record draft
# score" node is left out, and the workflow works the same without it.
EVALS_CRED = {"id": "ItO9HVMIcg2bDMOI", "name": "Portfolio AI evals"}
REPO_OUT = sys.argv[1]

STATE_LABELS = "ai-new, ai-analysing, ai-analysis-approved, ai-tests-approved, ai-done, ai-rejected"
JQL = (f"project = KAN AND issuetype = Story AND (labels IS EMPTY OR labels NOT IN ({STATE_LABELS})) "
       "ORDER BY created ASC")

FLATTEN = r"""
// Turn Jira's search response into one item per story, with the ADF description as plain text.
function adfText(node) {
  if (!node) return '';
  if (node.type === 'text') return node.text || '';
  if (node.type === 'hardBreak') return '\n';
  const inner = (node.content || []).map(adfText).join('');
  if (node.type === 'listItem') return '- ' + inner;
  if (['paragraph', 'heading', 'codeBlock', 'blockquote'].includes(node.type)) return inner + '\n';
  return inner;
}
const site = 'SITE_PLACEHOLDER';
const out = [];
for (const item of $input.all()) {
  for (const issue of item.json.issues || []) {
    out.push({ json: {
      key: issue.key,
      summary: issue.fields.summary,
      description: adfText(issue.fields.description).trim() || '(no description)',
      url: `${site}/browse/${issue.key}`,
    }});
  }
}
return out;
""".replace("SITE_PLACEHOLDER", SITE)

PROMPTS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prompts", "intake.json")))


def ollama_body(step, user_expr):
    """n8n expression for an Ollama chat request built from automation/prompts/intake.json, so the workflow and
    the evals (automation/evals/run.ts) always use the same prompt, schema, model and options."""
    p = PROMPTS[step]
    # n8n ends an expression at the first "}}", so JSON inside it must never close two braces in a row: the
    # schema is dumped with indentation, which puts each closing brace on its own line.
    body = ("JSON.stringify({ model: " + json.dumps(PROMPTS["model"]) + ", stream: false, think: false, "
            "options: " + json.dumps(PROMPTS["options"], indent=1) + ", format: " + json.dumps(p["schema"], indent=1)
            + ", messages: [{ role: 'system', content: " + json.dumps(p["system"]) + " }, "
            "{ role: 'user', content: " + user_expr + " }] })")
    return "={{ " + no_double_braces(body) + " }}"


def no_double_braces(expr):
    """Refuse to build an n8n expression that n8n would cut short at an inner "}}" (or open early at "{{")."""
    if "}}" in expr or "{{" in expr:
        raise ValueError("n8n expression contains '}}' or '{{', which n8n reads as the end of the expression")
    return expr


OLLAMA_BODY = ollama_body(
    "analysis",
    "'Story ' + $('Flatten stories').item.json.key + ': ' + $('Flatten stories').item.json.summary + "
    "'\\n\\n' + $('Flatten stories').item.json.description",
)

FORMAT = r"""
// Parse Qwen's JSON and build the Telegram message for this story.
const story = $('Flatten stories').item.json;
let a;
try { a = JSON.parse($json.message.content); }
catch (e) { a = { summary: '(could not parse model output)', ambiguities: [], risks: [] }; }
const list = (xs) => (xs && xs.length ? xs.map((x) => '• ' + x).join('\n') : '• none');
const text = [
  `🆕 New story ${story.key}: ${story.summary}`,
  story.url,
  '',
  `Summary: ${a.summary}`,
  '',
  'Ambiguities:', list(a.ambiguities),
  '',
  'Risks:', list(a.risks),
].join('\n').slice(0, 3800);
return { json: { ...story, analysis: a, text } };
"""

class _Resume:
    """Approval link for the waiting execution. Telegram rejects 'localhost' button URLs, so use 127.0.0.1."""
    @staticmethod
    def format(d, s):
        # resumeUrl already carries ?signature=..., so append with the right separator. `s` names the
        # approval step, so a button from an earlier message can't resume a later wait.
        return ("={{ ((u) => u + (u.includes('?') ? '&' : '?') + 'd=" + d + "&s=" + s + "')"
                "($execution.resumeUrl.replace('//localhost', '//127.0.0.1')) }}")
RESUME = _Resume()

class _AdfComment:
    """Jira comment body (ADF): bold header line, then one paragraph per non-empty line of `body` (an n8n expression)."""
    @staticmethod
    def format(header, body):
        return ("={{ JSON.stringify({ body: { type: 'doc', version: 1, content: ["
                f"{{ type: 'paragraph', content: [{{ type: 'text', text: {json.dumps(header)}, marks: [{{ type: 'strong' }}] }}] }}, "
                f"...String({body}).split('\\n').filter(l => l.trim()).map(l => ({{ type: 'paragraph', content: [{{ type: 'text', text: l }}] }}))"
                "] } }) }}")
ADF_COMMENT = _AdfComment()

def label_params(new_label, old_label="ai-new"):
    """Swap the story's `old_label` for `new_label`. One story per execution, so .first() is safe."""
    return {
        "method": "PUT",
        "url": f"={SITE}/rest/api/3/issue/{{{{ $('Format message').first().json.key }}}}",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "jiraSoftwareCloudApi",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": json.dumps({"update": {"labels": [{"remove": old_label}, {"add": new_label}]}}),
        "options": {},
    }

def decision_if(step):
    """IF node params: true only when the click was Approve for this step."""
    return {"conditions": {"string": [
        {"value1": "={{ $json.query ? $json.query.d : 'timeout' }}", "operation": "equal", "value2": "approve"},
        {"value1": "={{ $json.query ? $json.query.s : '' }}", "operation": "equal", "value2": step},
    ]}, "combineOperation": "all"}

def approval_buttons(step):
    return {"rows": [{"row": {"buttons": [
        {"text": "✅ Approve", "additionalFields": {"url": RESUME.format("approve", step)}},
        {"text": "❌ Reject", "additionalFields": {"url": RESUME.format("reject", step)}},
    ]}}]}

WAIT_24H = {"resume": "webhook", "httpMethod": "GET", "limitWaitTime": True, "limitType": "afterTimeInterval",
            "resumeAmount": 24, "resumeUnit": "hours", "options": {}}

TESTS_BODY = ollama_body(
    "testCases",
    "'Story ' + $('Format message').first().json.key + ': ' + $('Format message').first().json.summary + "
    "'\\n\\n' + $('Format message').first().json.description + '\\n\\nApproved analysis:\\n' + "
    "JSON.stringify($('Format message').first().json.analysis)",
)

def score_js():
    """automation/evals/score.ts as plain JavaScript (Node strips the types), so the live score in Telegram and the
    CI eval run the same code. Exports are removed because a Code node is a function body, not a module."""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evals", "score.ts")
    js = subprocess.run(
        ["node", "--input-type=module", "--no-warnings", "-e",
         "import { stripTypeScriptTypes } from 'node:module'; import { readFileSync } from 'node:fs';"
         "process.stdout.write(stripTypeScriptTypes(readFileSync(process.argv[1], 'utf8')))", src],
        check=True, capture_output=True, text=True).stdout
    return "\n".join(line[len("export "):] if line.startswith("export ") else line for line in js.splitlines())


FORMAT_TESTS_BODY = r"""
// Parse Qwen's test cases and build the approval message.
const story = $('Format message').first().json;
let cases = [];
try { cases = JSON.parse($json.message.content).cases || []; } catch (e) { cases = []; }
cases = cases.slice(0, 6);
const icon = { positive: '✅', negative: '⛔', edge: '⚠️' };
const blocks = cases.map((c, i) => [
  `${i + 1}. ${icon[c.type] || '•'} ${c.title} (${c.type})`,
  `   Covers: ${c.criterion}`,
  `   Pre: ${c.preconditions}`,
  ...(c.steps || []).map((s, j) => `   ${j + 1}) ${s}`),
  `   Expect: ${c.expected}`,
].join('\n'));
// Eval: the same scorer the CI gate uses (automation/evals/score.ts), so the reviewer sees evidence, not just text.
const evalScore = scoreTestCases({ key: story.key, summary: story.summary, description: story.description }, cases);
const evalLines = [
  `📊 ${evalScore.summary}`,
  ...evalScore.criteria.missing.map((m) => `   ✗ Not covered: ${m}`),
  ...evalScore.contradictions.map((c) => `   ✗ Case ${c.case}: ${c.reason}`),
  ...evalScore.duplicates.map(([a, b]) => `   ✗ Cases ${a} and ${b} look like duplicates`),
  ...evalScore.incomplete.map((c) => `   ✗ Case ${c.case}: ${c.reason}`),
];
const text = (cases.length
  ? [`🧪 Test cases for ${story.key}: ${story.summary}`, evalLines.join('\n'), ...blocks].join('\n\n')
  : `⚠️ Qwen returned no usable test cases for ${story.key}. Reject, then remove the ai-rejected label to retry.`
).slice(0, 3800);
return [{ json: { cases, text, evalScore, promptVersion: PROMPT_VERSION } }];
"""
FORMAT_TESTS = (score_js() + "\n" + FORMAT_TESTS_BODY).replace("PROMPT_VERSION", json.dumps(PROMPTS["version"]))

PREPARE_TESTS = r"""
// One Jira 'Test' issue per approved case, linked to the story with 'Tests' (story is tested by test).
const story = $('Format message').first().json;
const cases = $('Format test cases').first().json.cases;
const p = (t) => ({ type: 'paragraph', content: [{ type: 'text', text: String(t || '-') }] });
const h = (t) => ({ type: 'heading', attrs: { level: 3 }, content: [{ type: 'text', text: t }] });
return cases.map((c) => ({ json: { title: c.title, payload: {
  fields: {
    project: { key: 'KAN' },
    issuetype: { id: '10010' },
    summary: `[${story.key}] ${c.title}`.slice(0, 250),
    labels: ['ai-generated', `tc-${c.type}`],
    description: { type: 'doc', version: 1, content: [
      p(`Type: ${c.type} | Covers: ${c.criterion}`),
      h('Preconditions'), p(c.preconditions),
      h('Steps'),
      { type: 'orderedList', content: (c.steps || []).map((s) => ({ type: 'listItem', content: [p(s)] })) },
      h('Expected result'), p(c.expected),
    ] },
  },
  update: { issuelinks: [{ add: { type: { name: 'Tests' }, outwardIssue: { key: story.key } } }] },
} } }));
"""

SUMMARIZE = r"""
const story = $('Format message').first().json;
const site = story.url.split('/browse/')[0];
const keys = $input.all().map((i) => i.json.key);
const titles = $('Prepare test issues').all().map((i) => i.json.title);
const lines = keys.map((k, i) => `• ${k} ${titles[i] || ''}\n  ${site}/browse/${k}`);
return [{ json: { keys, text: [`🎉 Created ${keys.length} Test issues for ${story.key}:`, ...lines].join('\n') } }];
"""

def node(name, type_, version, pos, params, creds=None):
    n = {"name": name, "type": type_, "typeVersion": version, "position": pos, "parameters": params}
    if creds:
        n["credentials"] = creds
    return n

nodes = [
    node("Every minute", "n8n-nodes-base.scheduleTrigger", 1.2, [0, 0],
         {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}}),
    node("Search new stories", "n8n-nodes-base.httpRequest", 4.2, [220, 0], {
        "url": f"{SITE}/rest/api/3/search/jql",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "jiraSoftwareCloudApi",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "jql", "value": JQL},
            {"name": "fields", "value": "summary,description,issuetype,labels"},
            # One story per execution: each run pauses on its own approval link.
            {"name": "maxResults", "value": "1"},
        ]},
        "options": {},
    }, {"jiraSoftwareCloudApi": JIRA_CRED}),
    node("Flatten stories", "n8n-nodes-base.code", 2, [440, 0], {"jsCode": FLATTEN}),
    node("Claim story (label ai-new)", "n8n-nodes-base.httpRequest", 4.2, [660, 0], {
        "method": "PUT",
        "url": f"={SITE}/rest/api/3/issue/{{{{ $json.key }}}}",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "jiraSoftwareCloudApi",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": '{"update":{"labels":[{"add":"ai-new"}]}}',
        "options": {},
    }, {"jiraSoftwareCloudApi": JIRA_CRED}),
    node("Qwen analysis", "n8n-nodes-base.httpRequest", 4.2, [880, 0], {
        "method": "POST",
        "url": "http://127.0.0.1:11434/api/chat",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": OLLAMA_BODY,
        "options": {"timeout": 180000},
    }),
    node("Format message", "n8n-nodes-base.code", 2, [1100, 0],
         {"mode": "runOnceForEachItem", "jsCode": FORMAT}),
    node("Ask analysis approval", "n8n-nodes-base.telegram", 1.2, [1320, 0], {
        "chatId": CHAT_ID,
        "text": "={{ $json.text }}\n\nApprove this analysis? (buttons work from Telegram on this Mac)",
        "replyMarkup": "inlineKeyboard",
        "inlineKeyboard": approval_buttons("analysis"),
        "additionalFields": {"appendAttribution": False},
    }, {"telegramApi": TG_CRED}),
    node("Wait for analysis decision", "n8n-nodes-base.wait", 1.1, [1540, 0], WAIT_24H),
    node("Analysis approved?", "n8n-nodes-base.if", 1, [1760, 0], decision_if("analysis")),
    # Analysis approved
    node("Comment analysis on story", "n8n-nodes-base.httpRequest", 4.2, [1980, -160], {
        "method": "POST",
        "url": f"={SITE}/rest/api/3/issue/{{{{ $('Format message').first().json.key }}}}/comment",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "jiraSoftwareCloudApi",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": ADF_COMMENT.format(header="🤖 AI requirement analysis (approved)", body="$('Format message').first().json.text"),
        "options": {},
    }, {"jiraSoftwareCloudApi": JIRA_CRED}),
    node("Label analysis approved", "n8n-nodes-base.httpRequest", 4.2, [2200, -160],
         label_params("ai-analysis-approved"), {"jiraSoftwareCloudApi": JIRA_CRED}),
    node("Confirm approval", "n8n-nodes-base.telegram", 1.2, [2420, -160], {
        "chatId": CHAT_ID,
        "text": "=✅ {{ $('Format message').first().json.key }} analysis approved and posted to Jira. Drafting test cases…",
        "additionalFields": {"appendAttribution": False},
    }, {"telegramApi": TG_CRED}),
    # Test cases
    node("Qwen test cases", "n8n-nodes-base.httpRequest", 4.2, [2640, -160], {
        "method": "POST",
        "url": "http://127.0.0.1:11434/api/chat",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": TESTS_BODY,
        "options": {"timeout": 300000},
    }),
    node("Format test cases", "n8n-nodes-base.code", 2, [2860, -160], {"jsCode": FORMAT_TESTS}),
    node("Ask tests approval", "n8n-nodes-base.telegram", 1.2, [3080, -160], {
        "chatId": CHAT_ID,
        "text": "={{ $('Format test cases').first().json.text }}\n\nCreate these as Jira Test issues?",
        "replyMarkup": "inlineKeyboard",
        "inlineKeyboard": approval_buttons("tests"),
        "additionalFields": {"appendAttribution": False},
    }, {"telegramApi": TG_CRED}),
    node("Wait for tests decision", "n8n-nodes-base.wait", 1.1, [3300, -160], WAIT_24H),
    node("Tests approved?", "n8n-nodes-base.if", 1, [3520, -160], {
        **decision_if("tests"),
    }),
    # Tests approved: create them
    node("Prepare test issues", "n8n-nodes-base.code", 2, [3740, -320], {"jsCode": PREPARE_TESTS}),
    node("Create Test issue", "n8n-nodes-base.httpRequest", 4.2, [3960, -320], {
        "method": "POST",
        "url": f"{SITE}/rest/api/3/issue",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "jiraSoftwareCloudApi",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json.payload) }}",
        "options": {},
    }, {"jiraSoftwareCloudApi": JIRA_CRED}),
    node("Summarize created", "n8n-nodes-base.code", 2, [4180, -320], {"jsCode": SUMMARIZE}),
    node("Label done", "n8n-nodes-base.httpRequest", 4.2, [4400, -320],
         label_params("ai-done", "ai-analysis-approved"), {"jiraSoftwareCloudApi": JIRA_CRED}),
    node("Confirm tests created", "n8n-nodes-base.telegram", 1.2, [4620, -320], {
        "chatId": CHAT_ID,
        "text": "={{ $('Summarize created').first().json.text }}",
        "additionalFields": {"appendAttribution": False},
    }, {"telegramApi": TG_CRED}),
    # Tests rejected / timed out
    node("Label tests rejected", "n8n-nodes-base.httpRequest", 4.2, [3740, 0],
         label_params("ai-rejected", "ai-analysis-approved"), {"jiraSoftwareCloudApi": JIRA_CRED}),
    node("Confirm tests rejected", "n8n-nodes-base.telegram", 1.2, [3960, 0], {
        "chatId": CHAT_ID,
        "text": "=❌ {{ $('Format message').first().json.key }} test cases rejected; nothing was created. Remove the ai-rejected label to start over.",
        "additionalFields": {"appendAttribution": False},
    }, {"telegramApi": TG_CRED}),
    # Analysis rejected / timed out
    node("Comment rejection on story", "n8n-nodes-base.httpRequest", 4.2, [1980, 160], {
        "method": "POST",
        "url": f"={SITE}/rest/api/3/issue/{{{{ $('Format message').first().json.key }}}}/comment",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "jiraSoftwareCloudApi",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": ADF_COMMENT.format(
            header="🤖 AI analysis rejected (or no answer within 24h)",
            body="'Update the story, then remove the ai-rejected label to run the analysis again.'"),
        "options": {},
    }, {"jiraSoftwareCloudApi": JIRA_CRED}),
    node("Label rejected", "n8n-nodes-base.httpRequest", 4.2, [2200, 160],
         label_params("ai-rejected"), {"jiraSoftwareCloudApi": JIRA_CRED}),
    node("Confirm rejection", "n8n-nodes-base.telegram", 1.2, [2420, 160], {
        "chatId": CHAT_ID,
        "text": "=❌ {{ $('Format message').first().json.key }} analysis rejected. Edit the story and remove the ai-rejected label to re-run.",
        "additionalFields": {"appendAttribution": False},
    }, {"telegramApi": TG_CRED}),
]

def link(a, *targets_per_output):
    return {a: {"main": [[{"node": b, "type": "main", "index": 0} for b in outs] for outs in targets_per_output]}}

connections = {}
def chain(*names):
    for a, b in zip(names, names[1:]):
        connections.update(link(a, [b]))

chain("Every minute", "Search new stories", "Flatten stories", "Claim story (label ai-new)", "Qwen analysis",
      "Format message", "Ask analysis approval", "Wait for analysis decision", "Analysis approved?")
connections.update(link("Analysis approved?", ["Comment analysis on story"], ["Comment rejection on story"]))
if EVALS_CRED["id"]:
    # Record each draft's score on the QA dashboard. A failure never blocks the approval.
    nodes.append({**node("Record draft score", "n8n-nodes-base.httpRequest", 4.2, [2970, -320], {
        "method": "POST",
        "url": "https://portfolio.aswin-qa.workers.dev/api/ai-evals",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": ("={{ JSON.stringify({ kind: 'live', model: " + json.dumps(PROMPTS["model"]) + ", "
                     "promptVersion: $json.promptVersion, storyKey: $('Format message').first().json.key, "
                     "score: $json.evalScore.score, summary: $json.evalScore.summary.slice(0, 500) }) }}"),
        "options": {"timeout": 10000},
    }, {"httpHeaderAuth": EVALS_CRED}), "onError": "continueRegularOutput"})
    scored = ["Format test cases", "Record draft score"]
else:
    scored = ["Format test cases"]
chain("Comment analysis on story", "Label analysis approved", "Confirm approval", "Qwen test cases",
      *scored, "Ask tests approval", "Wait for tests decision", "Tests approved?")
connections.update(link("Tests approved?", ["Prepare test issues"], ["Label tests rejected"]))
chain("Prepare test issues", "Create Test issue", "Summarize created", "Label done", "Confirm tests created")
chain("Label tests rejected", "Confirm tests rejected")
chain("Comment rejection on story", "Label rejected", "Confirm rejection")
wf = {"name": "Jira Story Intake", "nodes": nodes, "connections": connections,
      "settings": {"executionOrder": "v1"}}

def call(method, path, body=None):
    req = urllib.request.Request(f"http://localhost:5678/api/v1{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} failed: {e.code} {e.read().decode()[:500]}")

WF_ID = "3WA6vkLhAEPxTvpS"
updated = call("PUT", f"/workflows/{WF_ID}", wf)
print("workflow updated:", updated["id"], "active:", updated.get("active"), "nodes:", len(updated["nodes"]))

# Repo copy: no chat id, no credential ids.
repo = json.loads(json.dumps(wf))
for n in repo["nodes"]:
    if n["type"] == "n8n-nodes-base.telegram":
        n["parameters"]["chatId"] = "<TELEGRAM_CHAT_ID>"
    for c in n.get("credentials", {}).values():
        c["id"] = "<set-in-n8n>"
os.makedirs(os.path.dirname(REPO_OUT), exist_ok=True)
with open(REPO_OUT, "w") as f:
    json.dump(repo, f, indent=2)
    f.write("\n")
print("wrote", REPO_OUT)
