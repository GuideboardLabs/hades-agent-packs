---
name: lcpp-tune-server
description: "Measuring a GGUF on this machine and picking the winning flags."
version: 1.0.0
license: MIT
tags: [llama-cpp, tuning, benchmark, vram]
---

# Tune the server on your own hardware

```bash
tools/lcpp-tune.py ~/models/model.gguf --dry-run       # the plan, five configs
tools/lcpp-tune.py ~/models/model.gguf                 # run it, ~2 to 6 minutes per config
tools/lcpp-tune.py ~/models/model.gguf --quick         # one config, one probe
tools/lcpp-tune.py ~/models/model.gguf --json tune.json
```

## What it guarantees

- **It refuses to measure a busy card.** If VRAM is already loaded it stops with the card's own
  numbers, because two models on one card produce numbers that describe neither.
- **It measures the card, not the flags.** After each load it reads `mem_info_vram_used` for every
  card, so a silent spill shows up as a number rather than as a mystery.
- **One unit per configuration**, stopped by name between runs, so a crashed server cannot serve
  the next data point.
- **It reads the server's timing line**, not wall clock, and records `finish_reason` so a config
  that hit a token cap is visible.
- **A failed config is recorded with the server's own error text** and excluded from the winner.
  Nothing is estimated or filled in.

## The sweep it runs

- MoE: five `-ncmoe` values around the fit estimate (estimate plus 8, 4, 0, -4, -8).
- Dense: `-ngl` at 0.9, 0.75, 0.6 and 1.0 of the layer count.
- Threads at physical core count (never logical).
- Context from `--ctx`, KV as `q8_0`, `-fa on`, `--timeout 1800`.

## Reading the result

The winner is the fastest config that is not more than 5 percent slower than the best while using
at least 0.3 GiB less VRAM. A config that is 3 percent slower and 0.6 GiB cooler is the better
server: it leaves room for a second process or a bigger context later.

Paste the printed `ExecStart` line into a unit, or run `tools/lcpp-serve.sh --from-report tune.json`.

## What it does not measure

- Long-generation drift: the probe generates about 96 tokens. Decode falls as KV grows, so re-check
  at production length before promising a number.
- Prefill on a 20k-token prompt: the probe is about 900 tokens. Batch sizes matter more there.
- Sampling quality: `temperature 0` for repeatability, and the card's own sampler is a separate
  decision recorded in the unit.
