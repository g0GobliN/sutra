# Hermes corpus results: fifteen scenarios, four conditions

This is the first end-to-end run of the branch inside a real Hermes Agent process, on a
corpus built for this comparison. It measures one thing well: how many tokens the model
receives after a tool result has been compressed. It does not measure task quality,
because the model in this run is a scripted stand-in, not a real assistant.

## Setup

- Hermes Agent v0.23.1, isolated home `~/.hermes-sutra-ab`, plugin copied from this
  branch (`sutra install` semantics, 38 files), provider `custom` pointing at a local
  OpenAI-compatible server on a free port.
- Each condition restarts the gateway before the run. The gateway keeps the environment
  of its first start, so reusing it silently mixes conditions; that was verified the
  hard way before this run.
- Four conditions: `baseline` (`SUTRA_ENABLED=0`), `compression-only` (search `off`),
  `balanced` (search `balanced`, language on, 4 passes), `aggressive` (search
  `aggressive`, language on, 4 passes).
- Fifteen scenarios, one per category in the brief, each one shell command whose output
  is the tool result: repository listing, source file, traceback, multi-file dump, test
  output, 12,000-number sequence, repeated status lines, markdown notes, 300-record JSON
  array, checksummed manifest, 3,000-line prose, several tool calls in one turn, a
  unified diff, a 20,000-number array, and a build log.
- The token column is an o200k_base count of the serialized final request. It is not
  provider-billed usage; the provider here is local.
- `SUTRA_REUSE=0` everywhere, so a repeated payload cannot be served from the exact
  cache and hide a mode difference.

## Results

| Scenario | baseline | compression-only | balanced | aggressive |
|---|---:|---:|---:|---:|
| 01 repository exploration | 15,071 | 8,026 (46.8%) | 8,024 (46.8%) | 8,022 (46.8%) |
| 02 code modification | 15,363 | 10,995 (28.4%) | 10,997 (28.4%) | 10,999 (28.4%) |
| 03 debugging traceback | 18,025 | 6,046 (66.5%) | 6,050 (66.4%) | 6,046 (66.5%) |
| 04 multi-file implementation | 19,629 | 11,025 (43.8%) | 11,029 (43.8%) | 11,025 (43.8%) |
| 05 test execution and repair | 15,737 | 5,287 (66.4%) | 5,283 (66.4%) | 5,285 (66.4%) |
| 06 long-context reasoning | 31,164 | 7,786 (75.0%) | 7,796 (75.0%) | 7,792 (75.0%) |
| 07 repeated tool operations | 15,505 | 13,663 (11.9%) | 13,643 (12.0%) | 13,651 (12.0%) |
| 08 documentation | 11,284 | 7,697 (31.8%) | 7,697 (31.8%) | 7,699 (31.8%) |
| 09 structured JSON output | 15,029 | 15,029 (0.0%) | 15,029 (0.0%) | 15,029 (0.0%) |
| 10 strict preservation | 16,518 | 3,073 (81.4%) | 3,085 (81.3%) | 3,103 (81.2%) |
| 11 repeated context | 15,793 | 5,293 (66.5%) | 4,329 (72.6%) | 4,325 (72.6%) |
| 12 multiple tool calls | 10,699 | 3,618 (66.2%) | 3,612 (66.2%) | 3,610 (66.3%) |
| 13 diff control | 6,084 | 6,084 (0.0%) | 6,084 (0.0%) | 6,084 (0.0%) |
| 14 large numeric array | 32,926 | 8,503 (74.2%) | 8,497 (74.2%) | 8,509 (74.2%) |
| 15 build log | 15,382 | 9,202 (40.2%) | 9,176 (40.4%) | 9,176 (40.4%) |

Mean over the fifteen scenarios: compression-only 46.6%, balanced 47.0%, aggressive
47.0%. Median wall time was 24.6 s for baseline, 24.5 s for compression-only, 23.5 s for
balanced, 25.4 s for aggressive; the spread between runs is larger than the differences
between conditions in this sample.

Raw report: `/tmp/sutra-ab/abc-corpus.json`, produced by
`python bench/agent/run_abc.py --corpus bench/agent/corpus --out /tmp/sutra-ab/abc-corpus.json`.

## What the run shows

- The branch plugin loads inside Hermes and compresses the tool result before the model
  call. Every compressed condition shows a real drop in request size, and the SUTRA
  records name the mode that produced it.
- The deterministic rules do the work. The search layer adds 6.1 points on the prose
  scenario (11) and 0.2 points on the build log (15), and is invisible on the rest.
- Aggressive mode buys nothing over balanced on this corpus while spending more time.
  That matches the earlier single-payload run (1,608 ms vs 377 ms for the same output).
- Two scenarios are controls and behave like controls: the diff stays byte-identical,
  and the structured JSON array is untouched.
- Scenario 09 is a genuine no-benefit case: 300 small JSON objects are re-serialized
  without a gain, so SUTRA keeps the original. That is the right outcome, and it is
  recorded here rather than hidden in the average.
- Scenario 14 deserves a note. Its 74% is not an array-preview win: the terminal tool
  truncates 129,051 characters to a head+tail window and a truncation note, and SUTRA
  then trims that envelope further. A raw numeric array with no envelope is still left
  alone, as `docs/json-array-preview.md` explains.
- Scenario 07 shows the limit of line-level rules: repeated *shapes* with different
  values are not duplicates, so only 11.9% comes off.

## What this run does not show

- **Task quality.** The provider is a script that answers "Inspected …"; no model
  reasoning happened, so no success rate can be computed. The harness supports a real
  provider through `--upstream`, and a free OpenRouter call was verified end to end
  (usage object with 201 tokens, cost 0), but a quality A/B needs the frozen protocol in
  `docs/ab-benchmark.md` and an evaluator, not a token counter.
- **Billed cost.** Token counts here are a tokenizer measurement of the request, not a
  provider invoice. `sutra cost --usage-file` accepts real usage metadata from OpenAI,
  Anthropic, Google and DeepSeek; none was produced in this run.
- **Multi-turn compounding.** Each scenario recorded two requests. In a long session the
  compressed result is re-sent on every later turn, so the per-result saving is a
  multiplier, not a one-off. That multiplier is not measured here.
- **Cache behaviour.** A local provider has no prompt cache, so no cached-input field is
  meaningful.

## Reproduction

```bash
python bench/agent/build_corpus.py
python bench/agent/run_abc.py --corpus bench/agent/corpus --out /tmp/abc.json \
  --work /tmp/abc-work --retries 2
python bench/agent/run_abc.py --report /tmp/abc.json
```

`--report` reprints a saved report, including the invalid-run filter. One condition
(the debugging scenario, compression-only) hit a local transport failure on the first
pass, was excluded by that filter, and was re-run cleanly; the table above uses the
clean run.
