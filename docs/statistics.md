# Statistics

This page explains every number evalroom prints, why it is computed the way it is, and what it does not tell you. The guiding rule: a rate never travels without its interval and its n.

## Wilson score interval

For `k` successes in `n` trials, evalroom uses the Wilson score interval (Wilson, 1927):

```
centre    = (p + z²/2n) / (1 + z²/n)
halfwidth = z · sqrt(p(1-p)/n + z²/4n²) / (1 + z²/n)
```

with `z = 1.96` for a two-sided 95% interval. The implementation is tested against Newcombe (1998), Table I, method 3 (for example 81/263 → 0.2553–0.3662 and 0/20 → 0–0.1611).

### Why not the Wald interval

The interval most people learn is `p ± z·sqrt(p(1-p)/n)`. It fails exactly where LLM evals live: small n and rates near 0 or 1.

| Result | Wald 95% | Wilson 95% |
| ------ | -------- | ---------- |
| 0/20   | 0%–0%    | 0%–16%     |
| 19/20  | 85%–105% | 76%–99%    |
| 12/20  | 39%–81%  | 39%–78%    |

Wald claims certainty at 0/20 and leaves [0, 1] at 19/20. Its actual coverage at small n is erratic and often far below the nominal 95% (Brown, Cai & DasGupta, 2001). Wilson stays inside [0, 1], is never zero-width, and has coverage close to nominal for the sample sizes human review produces. Clopper-Pearson would also be defensible; it is more conservative (wider) than needed for a decision gate.

## Why `minN` and `maxWidth`

Every `Interval` has `decidable = n >= minN && high - low <= maxWidth`, with defaults `minN = 50` and `maxWidth = 0.25`. `gate` refuses to decide on an undecidable interval, in either direction.

Width of the Wilson interval (low–high, total width) by n and observed rate:

| n   | p = 0.5      | p = 0.8      | p = 0.9      | p = 0.95     |
| --- | ------------ | ------------ | ------------ | ------------ |
| 12  | 25–75 (0.49) | 55–95 (0.40) | 65–99 (0.34) | 65–99 (0.34) |
| 20  | 30–70 (0.40) | 58–92 (0.34) | 70–97 (0.27) | 76–99 (0.23) |
| 30  | 33–67 (0.34) | 63–90 (0.28) | 74–97 (0.22) | 83–99 (0.16) |
| 50  | 37–63 (0.27) | 67–89 (0.22) | 79–96 (0.17) | 87–99 (0.12) |
| 100 | 40–60 (0.19) | 71–87 (0.16) | 83–94 (0.12) | 89–98 (0.09) |
| 200 | 43–57 (0.14) | 74–85 (0.11) | 85–93 (0.08) | 91–97 (0.06) |
| 400 | 45–55 (0.10) | 76–84 (0.08) | 87–93 (0.06) | 92–97 (0.04) |

Two separate guards are needed:

- **`maxWidth`** is the real criterion: an interval 34 points wide (n = 20, p = 0.8) cannot distinguish a good prompt from a mediocre one.
- **`minN`** exists because at very high or very low rates the Wilson interval can be narrow at small n (19/20 is 23 points wide) while the normal approximation behind it, and the representativeness of 20 cases, are both shaky. Fifty is a floor, not a target.

Both are options. Lower them deliberately, in code review, not to make a dashboard green.

### The gate rule

`pass` requires the _lower bound_ to clear the bar, not the point estimate. `fail` requires the point estimate to be below the bar. When the point estimate clears the bar but the lower bound does not, the honest answer is `undecidable`: it looks good, and you have not shown it yet. This makes `pass` conservative; if you want "probably at least 90%", set `minPassRate` to the bar and collect the cases.

The gate is a one-sample decision on one run. It is not a test that the candidate is better than the base. For that, look at the broken/fixed partition from `compareRuns` (a paired view of the same cases); a paired test such as McNemar's is on the roadmap.

## Kappa

### Fleiss' kappa across lenses

`agreementReport` computes Fleiss' kappa (Fleiss, 1971) with the lenses as raters and the verdicts as categories:

```
P_i  = (Σ_j n_ij² - m) / (m(m - 1))      agreement on item i, m = number of lenses
P̄    = mean of P_i
p_j  = share of all verdicts in category j
P_e  = Σ_j p_j²
κ    = (P̄ - P_e) / (1 - P_e)
```

Tested against the Wikipedia worked example (10 subjects, 14 raters, 5 categories, κ = 0.210) and hand-computed tables. Kappa is `null` when `P_e = 1` (every verdict identical), when there are fewer than two lenses, and below `minItems` (default 10) complete items.

**What it measures.** Whether the lenses converge on one verdict for an item. That was the question the production system needed answered ("if one lens passes it, do the others?"). It is not the reliability of any single lens. Lenses that look at different things are expected to disagree: in the bundled example a reply can be factually right and still promise a refund date policy forbids, so accuracy passes and policy fails. The example's κ = 0.06 is the correct reading of that data, not a bug: the lenses are doing different jobs.

**To measure grader reliability**, have two reviewers grade the same lens on the same items and compute `cohenKappa(labelsA, labelsB)` directly.

### Cohen's kappa per lens pair

Each lens pair gets Cohen's kappa (Cohen, 1960) and a 3×3 confusion matrix. Tested against the Wikipedia example (p_o = 0.7, p_e = 0.5, κ = 0.4). The confusion matrix is usually more useful than the number: it shows _which way_ two lenses disagree.

### Landis & Koch bands

`interpretKappa` uses Landis & Koch (1977): < 0 poor, 0–0.20 slight, 0.21–0.40 fair, 0.41–0.60 moderate, 0.61–0.80 substantial, 0.81–1 almost perfect. The authors called these bands arbitrary; they are a shared vocabulary, not thresholds with statistical meaning.

## Honest caveats

- **Kappa and prevalence.** When one verdict dominates (say 90% PASS), kappa can be low even when raw agreement is high, because chance agreement is high (Feinstein & Cicchetti, 1990). The report says so when any verdict exceeds 80% of all verdicts. Always read raw agreement and the confusion matrices alongside kappa.
- **Kappa at small n.** No interval is reported for kappa yet. At 19 items a kappa of 0.4 is compatible with a wide range of true values; the interpretation marks bands below 50 items as provisional. Bootstrap intervals are on the roadmap.
- **Clustered verdicts.** Prevalence intervals treat the `items × lenses` verdicts as independent trials. They are not: verdicts on the same item are correlated. Those intervals are narrower than they should be. Pass-rate intervals on runs do not have this problem (one result per case).
- **Golden-set bias.** Golden cases are promoted from items every lens passed. That set over-represents cases the system already handles well, so the golden pass rate is a regression signal, not an estimate of production quality. Add hard cases on purpose (promote with `requireUnanimousPass: false` after adjudication).
- **Multiple looks.** Re-running the gate after every few new cases and stopping when it says `pass` inflates the false-pass rate. Decide n in advance.
- **Rules are not reviewers.** A golden case's rules check what the promoter thought to write down. A candidate can pass every rule and still be worse. Keep sampling candidate outputs for human review.
- **Medians of review time** include superseded reviews: re-checking took time too.

## References

- Brown, L. D., Cai, T. T., & DasGupta, A. (2001). Interval estimation for a binomial proportion. _Statistical Science_, 16(2), 101–133.
- Cohen, J. (1960). A coefficient of agreement for nominal scales. _Educational and Psychological Measurement_, 20(1), 37–46.
- Feinstein, A. R., & Cicchetti, D. V. (1990). High agreement but low kappa: I. The problems of two paradoxes. _Journal of Clinical Epidemiology_, 43(6), 543–549.
- Fleiss, J. L. (1971). Measuring nominal scale agreement among many raters. _Psychological Bulletin_, 76(5), 378–382.
- Landis, J. R., & Koch, G. G. (1977). The measurement of observer agreement for categorical data. _Biometrics_, 33(1), 159–174.
- Newcombe, R. G. (1998). Two-sided confidence intervals for the single proportion: comparison of seven methods. _Statistics in Medicine_, 17(8), 857–872.
- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. _Journal of the American Statistical Association_, 22(158), 209–212.
