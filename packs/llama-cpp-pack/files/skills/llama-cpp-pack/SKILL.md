---
name: llama-cpp-pack
description: "Serving a GGUF with llama.cpp: pick, build, tune, serve."
version: 2.2.0
license: MIT
tags: [llama-cpp, serving, vram, moe, quant, vision, benchmarking, hades]
---

# llama.cpp serving pack

A portable kit for taking a GGUF from "file on disk" to "tuned, pre-tested, served and documented"
on a consumer gaming GPU. Docs live in the pack root; the work is done by nine tools and eight
sub-skills.

## Install contract

A pack that is copied onto a machine inherits the machine it was written on. Adapt first:

```bash
tools/lcpp-adapt.py                      # check every assumption, write the profile
tools/lcpp-install-skills.sh             # make the skills loadable
tools/lcpp-detect.py                     # what this box actually is
tools/lcpp-build.sh                      # backend by silicon, then verify the binary
tools/lcpp-recommend.py --scan ~/models  # what fits, with flags
tools/lcpp-recommend.py --choice         # six intents, three seats each: speed against depth
tools/lcpp-tune.py ~/models/model.gguf   # sweep, measure, pick the winner
tools/lcpp-smoke.py --url http://127.0.0.1:8115   # pre-test: template, thinking, tools, long ctx, speed
tools/lcpp-serve.sh --from-report tune.json --write --start
```

`lcpp-adapt.py` exits 3 when a hard assumption is false (no GPU, card busy, a binary missing a flag
the pack depends on). Stop there. On 0, or 4 with the conflicts understood, proceed.

Every tool runs on the standard library alone, needs no root, and leaves units disabled.

## Tools

| Tool | Job | Refuses to |
|---|---|---|
| `tools/pack-adapt.py` | the generic adaptation engine any pack can reuse | call an uncheckable assumption a pass |
| `tools/lcpp-checks.py` | this pack's domain checks: ROCm support, binary flags, Hades config, vision | assume the agent's config is sane |
| `tools/lcpp-adapt.py` | wrapper: the engine plus this pack's manifest | silently edit the pack or the config |
| `tools/lcpp-detect.py` | CPU/GPU/VRAM/GTT/backends/disks as JSON or text | guess a backend from a distro name |
| `tools/lcpp-gguf.py` | GGUF metadata without loading the model | trust a uint32 count (v3 uses uint64) |
| `tools/lcpp-build.sh` | cmake for vulkan/rocm/cuda/cpu, then verify `--mmproj` and `n-cpu-moe` | pick ROCm for silicon ROCm does not build for |
| `tools/lcpp-recommend.py` | fit math on real files and headers, ranked, with flags | print a speed number it has not measured |
| `tools/lcpp-tune.py` | fit, thread and context sweeps with VRAM accounting | measure while another model holds the card |
| `tools/lcpp-smoke.py` | pre-test the served model as an agent model | call a model that returns empty content "working" |
| `tools/lcpp-serve.sh` | unit generation plus an architecture check against the binary's source | leave a unit enabled |

## Sub-skills

- `lcpp-detect-system`: what the probe reports and how to read it critically.
- `lcpp-build-backend`: backend by GPU generation, build flags, verification.
- `lcpp-fit-model`: the arithmetic, the four verdicts, choosing between candidates.
- `lcpp-tune-server`: what a sweep guarantees and what it does not measure.
- `lcpp-serve-ops`: unit lines that matter, health versus identity, restore discipline.
- `lcpp-vision`: mmproj serving, placement on a shared card, client architectures.
- `lcpp-bench`: the credibility checklist for a benchmark.
- `lcpp-hades-setup`: the eight-step playbook for doing all of this for a Hades user.

## The twelve rules

1. One model per card.
2. Fit weights, then KV, then threads.
3. Never let VRAM overflow silently.
4. `-np 1` before blaming the model.
5. Quantize KV before shrinking the model.
6. `--threads` never exceeds physical cores.
7. Read the server's timing line, not wall clock.
8. `--load-mode none` when tensor overrides are in play.
9. Give thinking models 1,500+ tokens or they answer nothing.
10. Speculative decoding is not free.
11. A checksum proves a copy, not a source.
12. Restore production afterwards.

## Docs in this pack

`01-flags.md`, `02-vram-math.md`, `03-tuning-order.md`, `04-failures.md`, `05-hardware.md`,
`06-quants.md`, `07-benchmarking.md`, `08-field-reports.md` (X recon with permalinks),
`09-ops.md`, `10-vision.md`.

## Adding to it

A number earns its place by naming hardware, context, prompt depth, flags and whether the desktop
was idle. A tool earns its place by refusing to do the thing that produces a lie.
