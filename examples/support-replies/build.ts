/**
 * Regenerates golden.jsonl, run-base.jsonl and run-candidate.jsonl from the raw example data
 * (items, reviews, expected rules, outputs) using the library itself. Deterministic: fixed ids
 * and timestamps, so re-running produces identical files.
 *
 *   npm run example:build
 */

import { writeFile } from "node:fs/promises";
import { formatRunJsonl, promoteToGolden, scoreRun, toJsonl } from "../../src/index";
import { file, loadExpectedRules, loadItems, loadOutputs, loadReviews, loadRubric } from "./shared";

const rubric = await loadRubric();
const items = await loadItems();
const reviews = await loadReviews(rubric);
const expected = await loadExpectedRules();

const golden = [];
for (const item of items) {
  const rules = expected[item.id];
  if (!rules) continue;
  const result = promoteToGolden(item, reviews, {
    rubric,
    expectedRules: rules,
    promotedBy: "support-qa-lead",
    promotedAt: "2026-10-12T16:00:00.000Z",
  });
  if (!result.ok) {
    throw new Error(`${item.id}: ${result.error.map((refusal) => refusal.message).join("; ")}`);
  }
  golden.push(result.value);
}
await writeFile(file("golden.jsonl"), toJsonl(golden), "utf8");

const runs = [
  { name: "run-base.jsonl", outputs: "outputs-base.jsonl", id: "base", label: "reply-v3" },
  {
    name: "run-candidate.jsonl",
    outputs: "outputs-candidate.jsonl",
    id: "candidate",
    label: "reply-v4",
  },
];
for (const spec of runs) {
  const run = scoreRun(golden, await loadOutputs(spec.outputs), {
    id: spec.id,
    label: spec.label,
    createdAt: spec.id === "base" ? "2026-10-14T09:00:00.000Z" : "2026-10-15T09:00:00.000Z",
  });
  await writeFile(file(spec.name), formatRunJsonl(run), "utf8");
}

console.log(`wrote ${golden.length} golden cases and ${runs.length} runs`);
