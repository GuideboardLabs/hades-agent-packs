# The llama.cpp serving pack

Everything this machine knows about serving GGUF models with llama.cpp, in a form that works on a
Linux box it has never met: docs that explain, tools that measure, skills that load by name.

Built 2026-10-29 from local measurements, four long-running skills, and field reports harvested
from X. Every number names its hardware, context and prompt depth.

## Install contract: adapt, do not copy

A pack carries the machine it was written on. Run the middle man first, every time:

```bash
tools/lcpp-adapt.py          # every assumption checked against THIS machine
tools/lcpp-install-skills.sh # make the skills loadable by name
```

`pack.manifest.json` declares what the pack assumes (8 GiB of VRAM, an AMD card, port 8115 free,
weights on an internal disk, a build with `--mmproj` and `n-cpu-moe`, a Hades config pointing at a
live endpoint). `lcpp-adapt.py` checks each one, prints the false ones with a concrete remedy, and
writes `~/.config/pack-adapt/llama-cpp-pack.profile.json` for the other tools. It edits nothing and
hides nothing. Exit 3 means a hard assumption is false: stop there.

The engine is generic: `tools/pack-adapt.py` plus a per-pack `tools/lcpp-checks.py`. Any pack can
reuse it. The pattern is written up in the `agent-pack-adaptation` skill.

## Six commands on a new machine

```bash
tools/lcpp-adapt.py                     # what this pack assumes, and what is false here
tools/lcpp-detect.py                    # CPU, GPU, VRAM, backends, disks
tools/lcpp-build.sh                     # build llama.cpp for the right backend, then verify
tools/lcpp-recommend.py --scan ~/models # what fits here, with the flags to run it
tools/lcpp-tune.py ~/models/model.gguf  # measure the plausible configs, pick the winner
tools/lcpp-smoke.py --url http://127.0.0.1:8115   # pre-test it as an agent model
tools/lcpp-serve.sh --from-report tune.json --write --start
```

## Tools

| Tool | Job | What it refuses to do |
|---|---|---|
| `tools/lcpp-detect.py` | probe CPU, GPU, VRAM, GTT, backends, disks; text or JSON | guess a backend from the distro name |
| `tools/lcpp-gguf.py` | read GGUF metadata without loading the model | trust uint32 counts (v3 uses uint64) |
| `tools/lcpp-build.sh` | cmake for vulkan/rocm/cuda/cpu, then verify `--mmproj` and `n-cpu-moe` | pick ROCm for silicon ROCm does not build for |
| `tools/lcpp-recommend.py` | fit math on real file sizes and headers, ranked, with flags | print a speed number it has not measured |
| `tools/lcpp-tune.py` | fit, thread and context sweeps with VRAM accounting and a busy-card guard | measure while another model holds the card |
| `tools/lcpp-serve.sh` | unit generation, architecture check against the binary's source tree | leave a unit enabled |
| `tools/pack-adapt.py` | the adaptation engine, reusable by any pack | call an uncheckable assumption a pass |
| `tools/lcpp-checks.py` | this pack's domain checks: ROCm support, binary flags, Hades config, vision | assume the agent's config is sane |
| `tools/lcpp-adapt.py` | engine plus this pack's manifest | silently edit the pack or the config |
| `tools/lcpp-smoke.py` | pre-test the served model as an agent model: template, thinking budget, tool-call JSON, long context, speed | call empty content "working" |
| `tools/lcpp-install-skills.sh` | copy the sub-skills into a Hermes profile | install anything unasked |

`tools/catalog.json` holds model entries with the repository, the measured file size, MoE shape,
vision projector and any measurement taken on this hardware. Sizes are facts; speeds appear only
where they were measured.

## Sub-skills

Installable with `tools/lcpp-install-skills.sh`, loadable in Hermes by name:

- `lcpp-detect-system`: what the probe reports and how to read it critically.
- `lcpp-build-backend`: backend by GPU generation, build flags, verification.
- `lcpp-fit-model`: the arithmetic, the four verdicts, choosing between candidates.
- `lcpp-tune-server`: what a sweep guarantees, and what it does not measure.
- `lcpp-serve-ops`: unit lines that matter, health versus identity, restore discipline.
- `lcpp-vision`: mmproj serving, placement on a shared card, client architectures.
- `lcpp-bench`: the credibility checklist for a benchmark.
- `lcpp-hades-setup`: the eight-step playbook for doing all of this for a Hades user.

## Docs

| File | What is in it |
|---|---|
| `01-flags.md` | every flag that matters, what it does, and the trap attached |
| `02-vram-math.md` | budgeting VRAM, KV cache, GTT, and proving there is no spill |
| `03-tuning-order.md` | the order to tune in, with the sweep that proved each step here |
| `04-failures.md` | symptom to cause to fix, from real incidents |
| `05-hardware.md` | measured profiles: RDNA1, RDNA2/3, NVIDIA, T4, Apple, CPU |
| `06-quants.md` | quant ladder, KV quant, ternary, verifying a GGUF |
| `07-benchmarking.md` | method, admission floor, seeds, grading, and the four ways a result gets faked |
| `08-field-reports.md` | practitioners' configs from X, quoted with permalinks |
| `09-ops.md` | unit templates, port map, monitoring one-liners, restore discipline |
| `10-vision.md` | multimodal serving and sharing a card with a text model |

## The twelve rules

1. **One model per card.** Two large models do not share, they starve. The second one loads
   "successfully" with most layers forced to CPU and runs at a tenth of its speed.
2. **Fit weights first, then KV, then compute.** `-ngl` and `--n-cpu-moe` decide whether it runs.
   Everything else is tuning.
3. **Never let VRAM overflow silently.** The driver spills to GTT and the model keeps working at a
   fraction of speed with no error. Compare `mem_info_vram_used` against physical VRAM, not against
   hope.
4. **`-np 1` before you blame the model.** llama.cpp defaults to four parallel slots; each one
   multiplies the KV cache. One slot is what makes long context fit on small cards.
5. **Quantize the KV cache before you shrink the model.** `-ctk q8_0 -ctv q8_0` roughly halves KV
   memory and on this card cost nothing measurable (22.2 t/s at 12.8k prompt, 7.34 GB VRAM).
6. **`--threads` never exceeds physical cores.** Six cores means `-t 6`. Ten threads measured 2.87
   t/s where six measured 9.84.
7. **Read the server's own timing, not wall clock.** `slot print_timing` gives prompt and generation
   rates per request. Wall time includes model load, tokenization and the client.
8. **Disable mmap for big models.** `--load-mode none` took prefill from 48 t/s to 218 t/s on an
   18 GB model, because page faults during prompt processing are expensive.
9. **Give thinking models room or they return nothing.** A cap of 900 tokens produced 3,731
   characters of reasoning and an empty answer. Budget 1,500 to 2,500 tokens before the first word
   of the answer.
10. **Speculative decoding is not free.** It pays when draft acceptance is high and the draft is
    cheap. On a bandwidth-starved card with CPU-resident experts it measured 4 percent slower than
    plain decoding.
11. **A GGUF checksum proves a faithful copy, not a good source.** Verify `sha256` against the repo
    manifest, check magic bytes `47475546`, and read the live repo, not your memory of it.
12. **Restore production afterwards.** Stop the bench server, start what the household was using,
    and leave units `disabled` so a reboot does not load a model nobody asked for.

## How to use it as an agent

Install the skills with `tools/lcpp-install-skills.sh`, then load `llama-cpp-pack`. For a concrete
job the usual path is `lcpp-fit-model` to choose, `lcpp-tune-server` to measure, and
`lcpp-serve-ops` to install.

## Provenance

Local measurements: RX 5700 XT 8 GB (RDNA1), Ryzen 5 1600, 31 GB RAM, Linux 7.0, 2026-10.
Field reports: X posts collected 2026-10-29, quoted with permalinks in `08-field-reports.md`.
Build trees referenced here: `~/llama.cpp/build-vulkan`, `~/llama.cpp-moe-cache/build-vulkan`,
`/home/sc/clover-build` (PR 31306), `/mnt/ssd/llama-clover` (source clone).
