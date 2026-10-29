---
name: lcpp-bench
description: "Benchmarking a local model credibly: floor, seeds, grading."
version: 1.0.0
license: MIT
tags: [llama-cpp, benchmark, evaluation]
---

# Benchmark a model without fooling yourself

Read `07-benchmarking.md` in the pack for the long version. This is the checklist.

## Before the run

- Card idle: about 90 MB VRAM in use, no browser rendering anything, no second model.
- Token budget high enough that the answer is not cut off. A reasoning model spent 3,731 characters
  of thinking and returned empty at a 900-token cap.
- Sampler decided and recorded. A client that posts `temperature` overrides the unit's `--temp`.
- Context sized for the task, and the client's trim threshold *inside* the server's window.

## The run

- Three runs per event with distinct seeds; one run is a reference row, not a score.
- Hand-grade against the artifact. Partial credit is legitimate and expected.
- Disclose every deviation: context, sampler, thinking budget, quant, speculation.
- Admission floor: 10 t/s decode sustained. Below it the model is reference-only and flagged.

## After the run

- Record: file and quant, all flags, VRAM at load, prefill and decode at two prompt depths, wall
  time of a representative request, score per task, verdict.
- Stop the bench server, verify VRAM is back down, restore what the household was using.

## Failure modes that look like model faults

- A harness that pins `temperature 0.3` makes a card-sampler comparison meaningless.
- A client that trims at 1.5x the declared window cannot save a transcript before the server
  refuses it: the error arrives as an HTTP 400 mid-task.
- Thinking tokens consumed by the cap look exactly like an incompetent model.
- A busy desktop halves decode on identical flags. Check before, not after.
