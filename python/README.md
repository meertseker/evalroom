# evalroom

**You already correct scores in Inspect. evalroom tells you how often your judge was wrong, with a confidence interval.**

Human-in-the-loop quality control for LLM outputs: judge-vs-human agreement, multi-lens review, versioned rubrics, inter-rater agreement (Fleiss and Cohen kappa), golden sets and regression gates on Wilson intervals. Every rate carries its interval and n; nothing decides on too little data.

```sh
pip install evalroom                # core, zero dependencies, Python 3.10+
pip install "evalroom[inspect]"     # Inspect extension (Python 3.11+, like inspect_ai)
pip install "evalroom[pandas]"      # to_dataframe() helpers
```

```sh
evalroom judge-check logs/my-task.eval        # 0 acceptable, 1 not acceptable, 2 undecidable
```

```text
  Agreement      87% (52/60) · 76%–93%
  Judge wrong    13% (8/60) · 7%–24%
  Cohen's kappa  0.64  substantial
  Decision       UNDECIDABLE
  Reason         agreement 87% meets the 80% bar but the lower bound 76% does not; collect more labels
```

- `evalroom.inspect.judge_check(log)`: the judge's original grade (from `Score.history`) against the human edit, with agreement, kappa, a confusion matrix and the disagreements.
- `evalroom.inspect.wilson_accuracy()`: an Inspect metric with lower/upper bounds and a decidability flag, also registered as `evalroom/wilson_accuracy`.
- `evalroom gate` / `evalroom compare` on Inspect logs or on evalroom run files.
- The core API (`agreement_report`, `promote_to_golden`, `score_run`, `compare_runs`, `gate`, `wilson`, `cohen_kappa`, ...) and the `evalroom` CLI, identical in behaviour and file formats to the TypeScript package `evalroom` on npm.

Documentation, the worked example and the statistics write-up: <https://github.com/meertseker/evalroom>.

MIT licence.
