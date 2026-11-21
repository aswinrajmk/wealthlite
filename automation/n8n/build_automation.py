"""Build the 'Jira Test Automation' n8n workflow: Test issues labelled `smoke` or `regression` → Telegram
approval → repository_dispatch to qa-tests (automate-from-jira.yml), which writes the Playwright tests.

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

STATE_LABELS = "auto-pending, auto-queued, auto-pr-open, automated, auto-failed, auto-rejected"
JQL = (f"project = KAN AND issuetype = Test AND labels in (smoke, regression) AND labels not in ({STATE_LABELS}) "
       "ORDER BY created ASC")

GROUP = r"""
// Group the labelled Test issues by their story (Test --Tests--> Story) and take the oldest story only:
// one story per execution, because each run pauses on its own approval link.
const byStory = new Map();
for (const issue of $input.first().json.issues || []) {
  const link = (issue.fields.issuelinks || []).find((l) => l.type.name === 'Tests' && l.outwardIssue);
  if (!link) continue; // a Test with no story cannot be traced, so it is skipped
  const story = link.outwardIssue;
  const entry = byStory.get(story.key) || { story: story.key, title: story.fields.summary, tests: [] };
  const suite = issue.fields.labels.includes('smoke') ? 'smoke' : 'regression';
  entry.tests.push({ key: issue.key, suite, title: issue.fields.summary.replace(/^\[KAN-\d+\]\s*/, '') });
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
            {"name": "fields", "value": "summary,labels,issuelinks"},
            {"name": "maxResults", "value": "50"},
        ]},
        "options": {},
    }, {"jiraSoftwareCloudApi": JIRA_CRED}),
    code("Group by story", [440, 0], GROUP),
    code("Split tests (claim)", [660, 0], SPLIT),
    relabel("Label auto-pending", [880, 0], None, "auto-pending"),
    code("One per story (ask)", [1100, 0], BACK_TO_ONE),
    telegram("Ask automation approval", [1320, 0], "={{ $json.text }}\n\nWrite Playwright tests for these in qa-tests?",
             {"rows": [{"row": {"buttons": [
                 {"text": "✅ Automate", "additionalFields": {"url": _Resume.format("approve")}},
                 {"text": "❌ Not now", "additionalFields": {"url": _Resume.format("reject")}},
             ]}}]}),
    node("Wait for decision", "n8n-nodes-base.wait", 1.1, [1540, 0],
         {"resume": "webhook", "httpMethod": "GET", "limitWaitTime": True, "limitType": "afterTimeInterval",
          "resumeAmount": 24, "resumeUnit": "hours", "options": {}}),
    node("Approved?", "n8n-nodes-base.if", 1, [1760, 0], {"conditions": {"string": [
        {"value1": "={{ $json.query ? $json.query.d : 'timeout' }}", "operation": "equal", "value2": "approve"},
        {"value1": "={{ $json.query ? $json.query.s : '' }}", "operation": "equal", "value2": "automate"},
    ]}, "combineOperation": "all"}),
    # Approved
    code("Split tests (queue)", [1980, -160], SPLIT),
    relabel("Label auto-queued", [2200, -160], "auto-pending", "auto-queued"),
    code("One per story (dispatch)", [2420, -160], BACK_TO_ONE),
    node("Dispatch to qa-tests", "n8n-nodes-base.httpRequest", 4.2, [2640, -160], {
        "method": "POST",
        "url": "https://api.github.com/repos/aswinrajmk/qa-tests/dispatches",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Accept", "value": "application/vnd.github+json"}]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ event_type: 'jira-tests-labelled', client_payload: { story: $json.story } }) }}",
        "options": {},
    }, {"httpHeaderAuth": GITHUB_CRED}),
    telegram("Confirm dispatched", [2860, -160],
             "=🚀 {{ $('Group by story').first().json.story }}: qa-tests is writing the tests "
             "({{ $('Group by story').first().json.keys }}). You'll get the PR here."),
    # Rejected or no answer
    code("Split tests (reject)", [1980, 160], SPLIT),
    relabel("Label auto-rejected", [2200, 160], "auto-pending", "auto-rejected"),
    code("One per story (reject)", [2420, 160], BACK_TO_ONE),
    telegram("Confirm not automated", [2640, 160],
             "=⏸ {{ $json.story }}: not automated. Remove auto-rejected from a Test issue to ask again."),
]

connections = {}


def chain(*names):
    for a, b in zip(names, names[1:]):
        connections[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}


chain("Every minute", "Search labelled tests", "Group by story", "Split tests (claim)", "Label auto-pending",
      "One per story (ask)", "Ask automation approval", "Wait for decision", "Approved?")
connections["Approved?"] = {"main": [[{"node": "Split tests (queue)", "type": "main", "index": 0}],
                                     [{"node": "Split tests (reject)", "type": "main", "index": 0}]]}
chain("Split tests (queue)", "Label auto-queued", "One per story (dispatch)", "Dispatch to qa-tests",
      "Confirm dispatched")
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
