# Inspect log format: what evalroom reads

Observed with **inspect_ai 0.3.285** (Python 3.12) on a real log: a five-sample task, `generate()` solver and `model_graded_qa(model=...)` scorer, both models `mockllm/model` with `custom_outputs` callables (no API key), then two `edit_score` calls with `ProvenanceData`, `write_eval_log`, and `read_eval_log` again. The extension code is written against these observations, not against assumptions; if a later Inspect release changes them, this page and `python/src/evalroom/inspect/` change together.

## Files

- `.eval` is a zip archive: `header.json`, `summaries.json`, `reductions.json`, `samples/<id>_epoch_<n>.json`, `_journal/...`. `.json` logs hold the same data in one document. `read_eval_log(path)` reads both; `header_only=True` skips samples (`log.samples is None`).
- `write_eval_log(log, path)` rewrites the file; `format="json"` writes the single-document form.
- `log.location` is the path the log was read from or written to.

## Scores on a sample

- `sample.scores` is a `dict[str, Score]` keyed by **scorer name**. For `model_graded_qa` the key is `"model_graded_qa"` (the registry name without a package prefix). `log.results.scores[i].name` and `.scorer` carry the same name, and `log.eval.scorers` lists the scorers with their options.
- `sample.id` keeps its type (`str` or `int`); `sample.epoch` starts at 1. With several epochs the same id appears once per epoch.
- `Score` fields: `value`, `answer`, `explanation`, `reason`, `metadata`, `history`. For `model_graded_qa`, `value` is `"C"` / `"I"` (`"P"` with partial credit), `answer` is the submission, `explanation` is the grader's full completion (ending in `GRADE: C`), and `metadata["grading"]` holds the grader transcript.
- `sample.input` was a plain `str` in this task (it can be a list of chat messages); `sample.target` was a `str` (it can be a list).

## `edit_score` and `Score.history`

```python
edit_score(log, sample_id, "model_graded_qa",
           ScoreEdit(value="I", explanation="Ag is silver.",
                     provenance=ProvenanceData(author="ana", reason="Ag is silver, gold is Au")))
```

- A sample whose score was never edited has `history == []`.
- The first edit appends **two** entries: a snapshot of the original score (a `ScoreEdit` with `provenance=None`), then the edit itself. Every later edit appends one more entry. `Score.value` is updated in place, so after editing:

| field | meaning |
| --- | --- |
| `score.history[0]` | the original scorer output: `value`, `answer`, `explanation`, `metadata`; `provenance` is `None` |
| `score.history[1:]` | the edits, in order; each has `provenance` (`author`, `reason`, `timestamp`, `metadata`) |
| `score.value` | the current (edited) value |

- Fields an edit does not set are the literal string `"UNCHANGED"`. After a write and re-read, `None` fields of the original snapshot also come back as `"UNCHANGED"` (the log omits nulls and the model default is `"UNCHANGED"`), so `"UNCHANGED"` must be read as "not set".
- An edit that keeps the value (a reviewer confirming the grade) still appends a provenance entry: observed `history = [{value: "C", provenance: None}, {value: "C", provenance: {author: "ben", reason: "confirmed"}}]`.
- `edit_score` recomputes the log's metrics by default (`accuracy` went from 0.8 to 0.6 after correcting one sample) and appends a `ScoreEditEvent` to the sample's events.
- If the score name does not exist yet, `edit_score` creates it with `history == [edit]` (read from the 0.3.285 source): there is no original model score in that case.

## What evalroom derives from this

- **Judge label**: `history[0].value` when `history[0].provenance is None`; otherwise (no edit) the current `value`.
- **Human label**: the current `value` of a score whose history contains at least one entry with provenance. The last such entry gives the reviewer (`provenance.author`) and the reason (`provenance.reason`, falling back to the edit's `explanation`).
- **Not reviewed**: a score with no provenance entry. `judge-check` excludes these by default and reports how many there were.

## Metrics

Inspect metrics are `@metric` factories returning `Metric = Callable[[list[SampleScore]], Value]`, where `Value` includes `Mapping[str, str | int | float | bool | None]`. A metric may therefore return a dict of floats; `wilson_accuracy` returns `{"wilson_accuracy", "lower", "upper", "decidable", "n"}`. Inspect flattens a dict metric into the scorer's metric list, so a key named `accuracy` would collide with the built-in `accuracy()` and be renamed `accuracy2` (observed); hence the prefixed key. Inspect's own `value_to_float()` maps `C` to 1, `P` to 0.5, `I` and `N` to 0, `yes`/`true` to 1, `no`/`false` to 0, and numeric strings to their value.

Extension packages register through the `inspect_ai` entry-point group. Inspect loads them lazily on a registry lookup, and names objects from a package as `<package>/<name>`, so the metric is `evalroom/wilson_accuracy`.
