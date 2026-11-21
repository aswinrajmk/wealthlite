import assert from "node:assert/strict";
import { test } from "node:test";
import { dice, parseCriteria, scoreTestCases, type Story, type TestCase } from "./score.ts";

// The real KAN-6 run from 2026-11-20, as Qwen drafted it and it was approved by eye. Cases 1 and 3 say the user
// "has previously signed out and is logged in": the contradiction the dashboard calls out.
const KAN6: Story = {
  key: "KAN-6",
  summary: "Sign out from the portfolio page",
  description: [
    "As a signed-in investor, I want to sign out from the portfolio page so that nobody else can see my holdings on a shared computer.",
    "Acceptance criteria:",
    '- Given I am signed in, when I click "Sign out", then I return to the login page',
    "- Given I have signed out, when I open /portfolio directly, then I am sent to the login page",
    "- Given I have signed out, when I press the browser back button, then holdings are not shown",
  ].join("\n"),
};

const KAN6_DRAFT: TestCase[] = [
  {
    title: "Direct navigation after sign out redirects to login",
    type: "positive",
    criterion: "Given I have signed out, when I open /portfolio directly, then I am sent to the login page",
    preconditions: "User has previously signed out and is logged in",
    steps: [
      "Navigate directly to the URL /portfolio using the browser address bar.",
      "Observe that a login prompt or redirect occurs before accessing holdings.",
    ],
    expected: "The user is redirected to the login page instead of viewing portfolio data.",
  },
  {
    title: "Sign out redirects to login",
    type: "positive",
    criterion: 'Given I am signed in, when I click "Sign out", then I return to the login page',
    preconditions: '"I am signed in" and on the portfolio page',
    steps: [
      'Click the "Sign out" button located at the top right of the portfolio page.',
      "Observe that the browser address bar changes from /portfolio to /login",
    ],
    expected: "The user is redirected to the login page and cannot access holdings.",
  },
  {
    title: "Back button hides holdings",
    type: "positive",
    criterion: "Given I have signed out, when I press the browser back button, then holdings are not shown",
    preconditions: "User has previously signed out and is logged in",
    steps: [
      "Navigate to /portfolio from another page.",
      "Press the browser back button.",
      "Observe that the portfolio data is no longer visible.",
    ],
    expected: "The user returns to the previous page without seeing their holdings.",
  },
];

test("parses the acceptance criteria, without bullets", () => {
  assert.deepEqual(parseCriteria(KAN6.description), [
    'Given I am signed in, when I click "Sign out", then I return to the login page',
    "Given I have signed out, when I open /portfolio directly, then I am sent to the login page",
    "Given I have signed out, when I press the browser back button, then holdings are not shown",
  ]);
  assert.deepEqual(parseCriteria("Some text\nGiven x, when y, then z\nNo criterion here"), ["Given x, when y, then z"]);
});

test("the real KAN-6 draft: full coverage, but two cases contradict themselves", () => {
  const s = scoreTestCases(KAN6, KAN6_DRAFT);
  assert.deepEqual(s.criteria, { total: 3, covered: 3, missing: [] });
  assert.deepEqual(
    s.contradictions.map((c) => c.case),
    [1, 3],
  );
  assert.equal(s.duplicates.length, 0);
  assert.equal(s.incomplete.length, 0);
  assert.equal(s.score, 90); // 50 coverage + 15 no duplicates + 15 × (1 − 2/3) consistency + 10 + 10
  assert.equal(
    s.summary,
    "Score 90% · 3/3 criteria · 0 duplicates · ⚠ contradiction in case 1 · ⚠ contradiction in case 3",
  );
});

test("a corrected draft scores 100", () => {
  const fixed = KAN6_DRAFT.map((c) => ({ ...c, preconditions: c.preconditions.replace(" and is logged in", "") }));
  assert.equal(scoreTestCases(KAN6, fixed).score, 100);
});

test("a missing criterion, a duplicate and a bad size all cost points, and say so", () => {
  const twoAlike = [KAN6_DRAFT[1], { ...KAN6_DRAFT[1], title: "Sign out redirects to the login" }] as TestCase[];
  const s = scoreTestCases(KAN6, twoAlike);
  assert.equal(s.criteria.covered, 1);
  assert.equal(s.criteria.missing.length, 2);
  assert.deepEqual(s.duplicates, [[1, 2]]);
  assert.equal(s.sizeOk, false);
  assert.match(s.summary, /1\/3 criteria · 1 duplicate · ⚠ 2 cases \(want 3–6\)/);
  assert.equal(s.score, 42); // 50 × 1/3 + 0 duplicates + 15 + 10 + 0 size
});

test("flags incomplete cases with the reason", () => {
  const broken = [
    { ...KAN6_DRAFT[0], steps: ["Only one step"], expected: "", type: "happy" },
    KAN6_DRAFT[1],
    KAN6_DRAFT[2],
  ] as TestCase[];
  const s = scoreTestCases(KAN6, broken);
  assert.deepEqual(s.incomplete, [{ case: 1, reason: 'unknown type "happy", fewer than 2 steps, no expected result' }]);
});

test("no cases scores zero; 'not signed in' alone is not a contradiction", () => {
  assert.equal(scoreTestCases(KAN6, []).score, 0);
  const notSignedIn = [
    { ...KAN6_DRAFT[0], preconditions: "User is not signed in" },
    KAN6_DRAFT[1],
    KAN6_DRAFT[2],
  ] as TestCase[];
  assert.ok(!scoreTestCases(KAN6, notSignedIn).contradictions.some((c) => c.case === 1));
});

test("dice is 1 for identical text and low for unrelated text", () => {
  assert.equal(dice("Sign out", "sign out"), 1);
  assert.ok(dice("Sign out", "Export CSV") < 0.3);
});

test("a story without acceptance criteria is flagged as a story problem, not scored as missed coverage", () => {
  // KAN-26 on 2026-11-21: only a title, no description. The draft used to score 45% with "0/0 criteria".
  const titleOnly: Story = { key: "KAN-26", summary: "I need to sort my holdings according to value", description: "" };
  const s = scoreTestCases(
    titleOnly,
    KAN6_DRAFT.map((c) => ({ ...c, preconditions: "Signed in" })),
  );
  assert.equal(s.storyIssue, "Story has no acceptance criteria: fix the story first");
  assert.equal(s.criteria.total, 0);
  assert.equal(s.score, 100); // the other checks all pass, scaled to 100
  assert.match(
    s.summary,
    /^⚠ Story has no acceptance criteria: fix the story first · Score 100% · coverage not scored/,
  );
  assert.equal(scoreTestCases(KAN6, KAN6_DRAFT).storyIssue, null);
});
