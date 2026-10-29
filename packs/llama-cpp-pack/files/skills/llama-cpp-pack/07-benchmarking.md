# Benchmarking

A benchmark exists to answer one question: would this model, served this way, do this job well enough
and fast enough to use? Everything below protects that answer from being faked by accident.

## The admission floor

**10 t/s decode sustained.** Below that a model is reference-only: it gets a row marked flagged so
nobody builds a workflow on it, and no ranked score. The floor is about usability on an interactive
turn, not about the model's quality. On this machine the floor has produced these verdicts: Groot at
22 t/s ranked, Flint at 19 ranked, Clover at 10.29 borderline and left to the real run to decide.

## Three runs, real seeds, hand grading

- Three runs per event, distinct seeds, never one.
- Grade by hand against the actual artifact. A regex over the answer grades the formatting, not the
  work.
- Partial credit is legitimate. A task worth 3 points can score 2 for the right structure with one
  wrong edge case.
- Never let a token cap decide a score. If a model ran out of budget, that is a failure to report and
  often a config bug to fix, not a capability finding.
- Disclose every deviation from the reference config: context size, sampler, thinking budget,
  speculative decoding, quant.

## Measure throughput at production shape

The probe that matters matches the workload: same prompt length, same generation length, same client.
Decode drifts down as KV grows, and prefill varies with prompt length.

```
prompt 931 tok @ 285.0 t/s | decode 200 tok @ 10.29 t/s     # short and clean
prompt 4 tok @ 18.5 t/s    | decode 808 tok @ 9.39 t/s      # second request, cached prompt
prompt 46 tok @ 46.7 t/s   | decode 900 tok @ 10.17 t/s     # deeper generation
```

Read the server's `slot print_timing` line, not the client's wall clock. Wall clock includes model
load, tokenization, and whatever the client did before the request.

## The four ways a bench result gets faked

1. **The machine was not idle.** A browser tab rendering in software held four cores and halved
   decode. Two results on the same flags differed by 92 percent.
2. **The token cap ate the answer.** A reasoning model at a 900-token cap returned 3,731 characters of
   thinking and an empty answer. Scored naively, that is a zero for the model and a lie about it.
3. **The sampler was overridden by the client.** `--temp 1.0` in the unit file means nothing once the
   harness posts `"temperature": 0.3`. Check `/slots` to see what the request actually used.
4. **The harness trimmed too late.** A client trimming at `1.5 * cap` cannot save a transcript from a
   server wall at `cap`. The error surfaces as an HTTP 400 in the middle of a task, which reads like a
   model failure.

## Recording it

Per model, one row with: file name and quant, size, all server flags, context, VRAM and GTT at load,
prefill and decode at two prompt depths, wall time of a representative request, score per task,
and a one-line verdict. Everything else is conversation.

On this machine the harness is `~/Documents/agon-arena/scripts/run-benchmark.py` with SQLite at
`agon-bench.db`; model configs live in the `MODEL_CONFIG` dict in that script, and adding a fork means
three edits (binary path constant, a fork branch, and the RDNA1 env-guard list). Events are `agent`,
`code`, `reason`; `--runs 1` produces a reference row, three runs produce a ranked one.

## After the bench

Stop the bench server, verify VRAM is back to ~90 MB, start whatever the household was using, and
leave the units `disabled`. A bench that leaves a 20 GB model resident has cost more than it measured.
