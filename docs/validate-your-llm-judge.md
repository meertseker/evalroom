# Validate your LLM judge

An LLM judge (`model_graded_qa`, a rubric prompt, a pairwise grader) turns an eval into a number you can track. The number is only as good as the judge. This guide covers how to measure the judge against people, how many labels that takes, how to read the result, and how to run the check in Inspect and in CI.

## Why judge agreement matters

Everything downstream of the judge inherits its errors. If the judge accepts 6% of wrong answers and rejects 5% of right ones, a model that improves from 80% to 84% real accuracy can look flat, and a prompt change that teaches the model to phrase answers the way the judge likes can look like progress. Those are not hypothetical failure modes: judges are biased towards longer answers, their own phrasing, the first option, confident tone, and answers that restate the question.

You cannot fix that by looking at the eval's accuracy. You can fix it by looking at a sample of the judge's grades and recording where you disagree. Inspect already lets you do the recording; evalroom turns the record into a measurement with an error bar.

## How many labels you need, and why 50

Agreement is a proportion: of the samples a person reviewed, how many did the judge grade the same way. evalroom reports it with a Wilson 95% interval. The width of that interval is what decides how much you can conclude:

| reviewed | observed agreement 90% | observed agreement 80% |
| -------- | ---------------------- | ---------------------- |
| 20       | 70%–97% (0.27 wide)    | 58%–92% (0.34 wide)    |
| 50       | 79%–96% (0.17 wide)    | 67%–89% (0.22 wide)    |
| 100      | 83%–94% (0.12 wide)    | 71%–87% (0.16 wide)    |
| 200      | 85%–93% (0.08 wide)    | 74%–85% (0.11 wide)    |

At 20 labels, a judge that agreed 18 times out of 20 is compatible with anything from "excellent" to "wrong almost a third of the time". `judge-check` therefore refuses to decide below `--min-n 50` labels or when the interval is wider than `--max-width 0.25`, and says so: *do not trust this judge on this evidence yet*. Fifty is a floor, not a target; if your bar is 90% agreement, plan for 100–200 labels.

The decision rule is the one every gate in evalroom uses:

- **acceptable** (exit 0) when the interval's lower bound is at or above `--min-agreement` (default 0.8);
- **not acceptable** (exit 1) when the observed agreement is below the bar;
- **undecidable** (exit 2) otherwise: too few labels, too wide an interval, or an observed agreement above the bar whose lower bound is still below it. The example in the README is in that last case: 52/60 is 87%, but the interval reaches down to 76%.

Review a **random** sample. If people only open the grades that look wrong, the measured agreement says how good the judge is on suspicious cases, not on your eval.

## Kappa versus raw agreement

Raw agreement is easy to read and is what the decision uses. Its weakness is that it rewards a judge that says the common thing: if 90% of answers are correct, a judge that always says `C` agrees 90% of the time and is useless. Cohen's kappa corrects for that by comparing the agreement with what the two label distributions would produce by chance:

- kappa near 0 means the judge is no better than guessing with the right base rate, whatever the raw agreement;
- the Landis & Koch bands (slight, fair, moderate, substantial, almost perfect) are a convention for reading it, not a standard;
- kappa is depressed when one label dominates (the prevalence paradox), so a high raw agreement with a modest kappa is common on easy datasets. Read both.

evalroom withholds kappa below 10 reviewed samples, and when every label is identical (kappa is undefined then).

## Reading the confusion matrix

Rows are the judge's labels, columns the humans'. In the example:

```text
  Judge \ Human   C   I
  C              41   5
  I               3  11
```

- **C/I (5)**: the judge accepted answers people marked incorrect. These inflate your eval's accuracy; they are usually the more dangerous error.
- **I/C (3)**: the judge rejected correct answers, often over phrasing ("Three hundred sixty-six" for "366"). These deflate accuracy and penalise models that answer naturally.

The disagreement list underneath gives the input, the target, an excerpt of the judge's reasoning and the reviewer's reason for each case, which is usually enough to see whether the fix belongs in the judge prompt, the target, or the dataset.

## Partial credit

`model_graded_qa(partial_credit=True)` and some custom scorers emit `P`. A reviewer may also correct a grade to `P`. evalroom has to compare labels, so it needs a rule:

- `--partial fail` (default): `P` counts as `I`. Conservative: half right is not right.
- `--partial pass`: `P` counts as `C`.
- `--partial exclude`: samples where either label is `P` are left out, and the count is reported.

Numeric scores strictly between 0 and 1 are treated as partial credit too. The same option exists on `evalroom gate` and `compare` for Inspect logs, and on the `wilson_accuracy` metric.

## The workflow in Inspect

1. Run the eval: `inspect eval my_task.py --model ...`. Keep the `.eval` log.
2. Review a random sample of grades. Either open the log in `inspect view` and edit scores there, or script it:

   ```python
   from inspect_ai.log import ProvenanceData, edit_score, read_eval_log, write_eval_log
   from inspect_ai.scorer import ScoreEdit

   log = read_eval_log("logs/my-task.eval")
   edit_score(log, "q68", "model_graded_qa",
              ScoreEdit(value="I", provenance=ProvenanceData(author="ana", reason="Au is gold.")))
   edit_score(log, "q12", "model_graded_qa",          # a confirmation: same value, still recorded
              ScoreEdit(value="C", provenance=ProvenanceData(author="ana", reason="Checked.")))
   write_eval_log(log, log.location)
   ```

   Record confirmations, not only corrections. A sample you looked at and agreed with is evidence; a sample you never opened is not, and evalroom will not count it unless you pass `--assume-unedited-agree`.
3. Check the judge: `evalroom judge-check logs/my-task.eval`.
4. If labels live elsewhere (a spreadsheet, an annotation tool), export them as JSONL `{"sampleId": "q68", "epoch": 1, "label": "I", "reason": "..."}` or CSV with `sampleId,epoch,label,reason` columns and pass `--human labels.csv`. The file then defines the reviewed set; `epoch` is only needed when the log has several.

How evalroom reads `Score.history` is described in [inspect-log-format.md](inspect-log-format.md).

## Judge-check in CI

`judge-check` exits 0, 1 or 2, so it can guard a pipeline the same way `evalroom gate` does:

```yaml
- run: inspect eval tasks/qa.py --model ${{ vars.MODEL }} --log-dir logs
- run: pip install "evalroom[inspect]"
- run: evalroom judge-check "$(ls -t logs/*.eval | head -1)" --min-agreement 0.85 --json > judge-check.json
```

Two practical points:

- an undecidable result (exit 2) usually means "label more", not "the judge is bad". Decide up front whether CI treats 2 as a failure; many teams fail on 1 and warn on 2 until enough labels exist;
- keep the labels with the log. A judge prompt change invalidates old agreement numbers the same way a rubric change does: re-review a fresh sample after changing the judge.

## What the check does not tell you

- It measures agreement with your reviewers, not truth. If reviewers disagree with each other, measure that first (two people on the same samples, `cohen_kappa`, or `evalroom agreement` with one lens per reviewer).
- It is an average. A judge can be fine overall and bad on one category; split the log by category and check each if that matters.
- A judge that is acceptable for one task is not automatically acceptable for another, or for a different model under test whose answers look different.
