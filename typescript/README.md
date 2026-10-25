# evalroom

Human-in-the-loop quality control for LLM outputs: multi-lens review, versioned rubrics, inter-rater agreement, golden sets and gated regression runs. No bare pass rate ever leaves.

```sh
npm install evalroom
```

```ts
import { agreementReport, defineRubric, formatInterval, gate } from "evalroom";

const rubric = defineRubric({
  version: "2026-10.1",
  lenses: ["accuracy", "tone"],
  errorClasses: [{ code: "WRONG_FACT", label: "Wrong fact", lenses: ["accuracy"],
    include: "States something false.", exclude: "Incomplete but not false." }],
});

const report = agreementReport(reviews, rubric);
console.log(report.fleissKappa, formatInterval(report.rawAgreement)); // "58% (11/19) · 36%–77% · not decidable"
console.log(gate(run, { minPassRate: 0.9 }).decision);               // "pass" | "fail" | "undecidable"
```

- Wilson intervals with a decidability flag on every rate (`minN` 50, `maxWidth` 0.25), Fleiss and Cohen kappa with Landis & Koch bands.
- Rubric versioning, latest-verdict-per-lens review logic, agreement reports with confusion matrices and disagreements.
- Golden promotion with every refusal reason, a generic rule engine, run comparison (broken / fixed / same) and gates.
- The `evalroom` CLI: `agreement`, `gate`, `compare`, `golden promote`, `score`, all with `--json`.

ESM and CommonJS, type declarations, Node 20+, zero runtime dependencies. The Python package `evalroom` on PyPI has the same API, file formats and CLI, plus an Inspect extension that checks LLM judges against human score edits.

Documentation and the worked example: <https://github.com/meertseker/evalroom>. MIT licence.
