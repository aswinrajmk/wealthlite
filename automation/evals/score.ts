// Scores a set of manual test cases drafted by the local model for one Jira story.
//
// LLM output changes wording on every run, so this does not compare text. It checks properties a QA reviewer
// checks by eye, deterministically, and explains every point it takes off:
//   coverage      every acceptance criterion has at least one case             (50 points)
//   duplicates    no two cases test the same thing                             (15)
//   consistency   no case contradicts itself ("signed out and is logged in")   (15)
//   completeness  each case has a criterion, preconditions, 2-5 steps, a result (10)
//   size          3 to 6 cases, the minimal set the prompt asks for            (10)
// Used live (n8n shows the score in the Telegram approval) and in CI (the golden set gates prompt and model changes).

export interface Story {
  key: string;
  summary: string;
  description: string;
}

export interface TestCase {
  title: string;
  type: string;
  criterion: string;
  preconditions: string;
  steps: string[];
  expected: string;
}

export interface Score {
  /** 0–100. */
  score: number;
  criteria: { total: number; covered: number; missing: string[] };
  duplicates: [number, number][];
  contradictions: { case: number; reason: string }[];
  incomplete: { case: number; reason: string }[];
  sizeOk: boolean;
  /** Set when the story itself is the problem (no acceptance criteria); the score then excludes coverage. */
  storyIssue: string | null;
  /** One line for Telegram, e.g. "Score 82% · 3/3 criteria · 0 duplicates · ⚠ contradiction in case 2". */
  summary: string;
}

const WEIGHTS = { coverage: 50, duplicates: 15, consistency: 15, completeness: 10, size: 10 } as const;
const COVER_THRESHOLD = 0.6;
const DUPLICATE_THRESHOLD = 0.8;
const TYPES = new Set(["positive", "negative", "edge"]);

const STOPWORDS = new Set(
  (
    "a an the and or but if then when given i my me we our you your is are was were be been am to of in on at by " +
    "for with from as it its this that these those can not no do does did has have had will should would shall " +
    "so into out up any all each user page"
  ).split(" "),
);

/** Content words, lowercased and lightly stemmed, so "signed"/"sign" and "holdings"/"holding" match. */
export function keywords(text: string): Set<string> {
  const words = text
    .toLowerCase()
    .replace(/[^a-z0-9/ ]+/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 2 && !STOPWORDS.has(w))
    .map((w) => w.replace(/(ing|ed|es|s)$/, ""));
  return new Set(words.filter((w) => w.length > 1));
}

/**
 * Acceptance criteria from a story description: the lines after "Acceptance criteria", or any Given/When/Then line.
 * Bullets and numbering are stripped.
 */
export function parseCriteria(description: string): string[] {
  const lines = description.split("\n").map((l) => l.trim());
  const start = lines.findIndex((l) => /^acceptance criteria:?$/i.test(l));
  const candidates = start >= 0 ? lines.slice(start + 1) : lines.filter((l) => /\bthen\b/i.test(l));
  return candidates
    .map((l) => l.replace(/^([-*•]|\d+[.)])\s*/, "").trim())
    .filter((l) => l.length > 0 && !/^(notes?|out of scope)\b/i.test(l));
}

/** Share of the criterion's keywords that the case mentions (in its criterion, title, steps or expected result). */
function overlap(criterion: string, c: TestCase): number {
  const want = keywords(criterion);
  if (!want.size) return 0;
  const have = keywords([c.criterion, c.title, ...c.steps, c.expected].join(" "));
  let hit = 0;
  for (const w of want) if (have.has(w)) hit++;
  return hit / want.size;
}

function bigrams(text: string): string[] {
  const t = text.toLowerCase().replace(/\s+/g, " ").trim();
  const out: string[] = [];
  for (let i = 0; i < t.length - 1; i++) out.push(t.slice(i, i + 2));
  return out;
}

/** Sørensen–Dice similarity on character bigrams, 0–1 (the same measure the qa-tests healer uses). */
export function dice(a: string, b: string): number {
  const x = bigrams(a);
  const y = bigrams(b);
  if (!x.length || !y.length) return a.trim().toLowerCase() === b.trim().toLowerCase() ? 1 : 0;
  const counts = new Map<string, number>();
  for (const g of x) counts.set(g, (counts.get(g) ?? 0) + 1);
  let both = 0;
  for (const g of y) {
    const n = counts.get(g) ?? 0;
    if (n > 0) {
      both++;
      counts.set(g, n - 1);
    }
  }
  return (2 * both) / (x.length + y.length);
}

// States that cannot both hold in one precondition. Extend this list when a review finds a new kind.
const OPPOSITES: { a: RegExp; b: RegExp; reason: string }[] = [
  {
    a: /\b(signed|logged) out\b|\bnot (signed|logged) in\b/i,
    b: /(?<!not )\b(signed|logged) in\b/i,
    reason: "signed out and signed in at the same time",
  },
  {
    a: /\b(empty|blank)\b/i,
    b: /\b(filled|completed|entered)\b/i,
    reason: "a field that is both empty and filled",
  },
  {
    a: /\binvalid\b/i,
    b: /(?<!in)\bvalid\b/i,
    reason: "data that is both valid and invalid",
  },
];

export function scoreTestCases(story: Story, cases: TestCase[]): Score {
  const criteria = parseCriteria(story.description);

  const missing = criteria.filter((crit) => !cases.some((c) => overlap(crit, c) >= COVER_THRESHOLD));
  const covered = criteria.length - missing.length;
  const coverage = criteria.length ? covered / criteria.length : 0;

  const duplicates: [number, number][] = [];
  for (let i = 0; i < cases.length; i++) {
    for (let j = i + 1; j < cases.length; j++) {
      const a = cases[i];
      const b = cases[j];
      if (!a || !b) continue;
      const sameTitle = dice(a.title, b.title) >= DUPLICATE_THRESHOLD;
      const sameTest = dice(a.criterion, b.criterion) >= 0.95 && dice(a.expected, b.expected) >= DUPLICATE_THRESHOLD;
      if (sameTitle || sameTest) duplicates.push([i + 1, j + 1]);
    }
  }

  const contradictions: Score["contradictions"] = [];
  cases.forEach((c, i) => {
    for (const o of OPPOSITES) {
      if (o.a.test(c.preconditions) && o.b.test(c.preconditions))
        contradictions.push({ case: i + 1, reason: o.reason });
    }
  });

  const incomplete: Score["incomplete"] = [];
  cases.forEach((c, i) => {
    const problems = [
      !c.title?.trim() && "no title",
      !TYPES.has(c.type) && `unknown type "${c.type}"`,
      !c.criterion?.trim() && "no criterion",
      !c.preconditions?.trim() && "no preconditions",
      (!Array.isArray(c.steps) || c.steps.filter((s) => s.trim()).length < 2) && "fewer than 2 steps",
      Array.isArray(c.steps) && c.steps.length > 5 && "more than 5 steps",
      !c.expected?.trim() && "no expected result",
    ].filter((p): p is string => Boolean(p));
    if (problems.length) incomplete.push({ case: i + 1, reason: problems.join(", ") });
  });

  const sizeOk = cases.length >= 3 && cases.length <= 6;
  // A story without acceptance criteria is a requirement problem, not a model problem: coverage cannot be judged,
  // so it is left out and the other checks are scaled to 100. The story issue is reported first.
  const storyIssue = criteria.length ? null : "Story has no acceptance criteria: fix the story first";
  const otherPoints =
    WEIGHTS.duplicates * (1 - Math.min(duplicates.length / Math.max(cases.length - 1, 1), 1)) +
    WEIGHTS.consistency * (cases.length ? 1 - new Set(contradictions.map((c) => c.case)).size / cases.length : 0) +
    WEIGHTS.completeness * (cases.length ? 1 - incomplete.length / cases.length : 0) +
    WEIGHTS.size * (sizeOk ? 1 : 0);
  const points = storyIssue
    ? (otherPoints * 100) / (100 - WEIGHTS.coverage)
    : WEIGHTS.coverage * coverage + otherPoints;
  const score = cases.length ? Math.round(points) : 0;

  const flags = [
    ...(storyIssue ? [`⚠ ${storyIssue}`] : []),
    `Score ${score}%`,
    storyIssue ? "coverage not scored" : `${covered}/${criteria.length} criteria`,
    `${duplicates.length} duplicate${duplicates.length === 1 ? "" : "s"}`,
    ...[...new Set(contradictions.map((c) => c.case))].map((n) => `⚠ contradiction in case ${n}`),
    ...(incomplete.length ? [`⚠ ${incomplete.length} incomplete`] : []),
    ...(sizeOk ? [] : [`⚠ ${cases.length} cases (want 3–6)`]),
  ];

  return {
    score,
    criteria: { total: criteria.length, covered, missing },
    duplicates,
    contradictions,
    incomplete,
    sizeOk,
    storyIssue,
    summary: flags.join(" · "),
  };
}
