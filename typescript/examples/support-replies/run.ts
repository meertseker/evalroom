/**
 * Walks the support-replies example end to end with the public API:
 * agreement → golden promotion (including two refusals) → base vs candidate → gate.
 *
 *   npm run example     (or: npx tsx examples/support-replies/run.ts)
 */

import { readFile } from "node:fs/promises";
import {
  agreementReport,
  compareRuns,
  formatInterval,
  type GoldenCase,
  gate,
  parseJsonl,
  parseRunJsonl,
  promoteToGolden,
} from "../../src/index";
import { file, loadExpectedRules, loadItems, loadReviews, loadRubric } from "./shared";

const rubric = await loadRubric();
const items = await loadItems();
const reviews = await loadReviews(rubric);

// 1. Can we trust the graders?
const report = agreementReport(reviews, rubric);
console.log(`# Agreement (rubric ${report.rubricVersion})`);
console.log(
  `complete items:   ${report.items} (${report.incompleteItems} incomplete, ${report.excludedReviews} old-rubric reviews excluded)`,
);
console.log(`raw agreement:    ${formatInterval(report.rawAgreement)}`);
console.log(
  `Fleiss' kappa:    ${report.fleissKappa.value?.toFixed(2) ?? "n/a"} (${report.fleissKappa.band ?? report.fleissKappa.reason})`,
);
for (const pair of report.pairs) {
  console.log(
    `  ${`${pair.a} × ${pair.b}`.padEnd(18)} ${formatInterval(pair.agreement)} · Cohen ${pair.cohenKappa.value?.toFixed(2) ?? "n/a"}`,
  );
}
console.log(`disagreements:    ${report.disagreements.map((d) => d.itemId).join(", ")}`);
console.log(report.interpretation);

// 2. Golden promotion refuses what it should.
const rules = await loadExpectedRules();
const fallbackRules = rules["t-01"] ?? [];
for (const id of ["t-03", "t-18"]) {
  const item = items.find((candidate) => candidate.id === id);
  if (!item) continue;
  const result = promoteToGolden(item, reviews, {
    rubric,
    expectedRules: fallbackRules.slice(0, 1),
    promotedBy: "support-qa-lead",
  });
  if (!result.ok) {
    console.log(`\n# Promote ${id}: refused`);
    for (const refusal of result.error) console.log(`  ${refusal.code}: ${refusal.message}`);
  }
}
const golden = parseJsonl(await readFile(file("golden.jsonl"), "utf8")) as GoldenCase[];
console.log(`\n# Golden set: ${golden.length} cases promoted from unanimous PASS items`);

// 3. Is the candidate prompt better?
const base = parseRunJsonl(await readFile(file("run-base.jsonl"), "utf8"), "run-base.jsonl");
const candidate = parseRunJsonl(
  await readFile(file("run-candidate.jsonl"), "utf8"),
  "run-candidate.jsonl",
);
const comparison = compareRuns(base, candidate);
console.log(`\n# ${base.label} → ${candidate.label}`);
console.log(`${base.label.padEnd(9)} ${formatInterval(comparison.baseSummary.passRate)}`);
console.log(`${candidate.label.padEnd(9)} ${formatInterval(comparison.candidateSummary.passRate)}`);
console.log(`fixed:   ${comparison.fixed.map((row) => row.caseId).join(", ")}`);
console.log(
  `broken:  ${comparison.broken.map((row) => `${row.caseId} (${row.candidate.failures.map((f) => f.code).join(", ")})`).join(", ")}`,
);
console.log(`same:    ${comparison.same.length}`);

// 4. Can we ship it?
const decision = gate(candidate, { minPassRate: 0.8 });
console.log(`\n# Gate (min pass rate 80% on the lower bound, minN 50)`);
console.log(`${decision.decision.toUpperCase()}: ${decision.reason}`);
