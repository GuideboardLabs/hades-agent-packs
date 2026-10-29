---
name: lcpp-fit-model
description: "Choosing a model and quant that fits this GPU, with flags."
version: 1.0.0
license: MIT
tags: [llama-cpp, model-choice, vram, moe, quant]
---

# Pick a model and a quant that fit

```bash
tools/lcpp-recommend.py                          # detect, scan, rank
tools/lcpp-recommend.py --scan /data/gguf --ctx 32768
tools/lcpp-recommend.py --downloads              # models worth fetching that fit
tools/lcpp-recommend.py --all                    # show what was rejected and by how much
tools/lcpp-recommend.py --json | jq '.candidates[0]'
```

The script reads every local GGUF's header (block count, expert count, head counts, context) with
`tools/lcpp-gguf.py`, so the arithmetic is on the real file, not on a name.

## The fit arithmetic

```
budget   = VRAM * 0.94 - 0.15 (desktop)      # ~7.5 GiB on an 8 GiB card
need     = weights + kv + overhead           # overhead ~0.6 GiB, tunable with --overhead
kv       = 2 * kv_heads * head_dim * full_attn_layers * bytes_per_element * ctx
```

KV is where most hand estimates go wrong in both directions. Hybrid architectures (gated delta
net, linear attention) keep O(1) state on most layers, so a 19B hybrid at 32k needs ~0.16 GiB of KV
while a dense 27B at 32k needs several. The tool knows the fraction for the architectures it has
seen (`qwen35moe`, `clover_moe`, `lfm3`, `nemotron_h`) and otherwise assumes half the layers carry
full attention, labelled as an estimate.

## The four verdicts

- **FULL**: weights and KV both fit. `-ngl 999`, pick the largest context whose KV still fits.
- **OFFLOAD** (MoE only): experts to RAM. The tool proposes `-ncmoe` and a sweep band. Sharp
  cliff: measured 10.29 t/s at 8.05 GiB, 2.20 t/s at 8.53 GiB on the same file.
- **PARTIAL** (dense): spend GPU layers. Gemma 4 12B wants `-ngl 50` at 32k on 8 GiB.
- **RAM**: it runs, slowly, from system memory. Fine for a batch job, not for a chat.

## Choosing between candidates

Prefer, in order: fully resident over offloaded; larger parameters over smaller at the same fit;
imatrix or dynamic quants over plain ones at the same size; a model with a measured number on this
hardware over one without. Speed claims without a measurement are not printed by this tool, on
purpose.

## Reality checks the tool cannot do for you

- A model that fits in VRAM may still be unusable if its architecture has no kernel support in
  your build (`grep LLM_ARCH_*`), or if the chat template returns an empty `content` for its
  architecture family.
- Two models do not share a card. A recommendation is a recommendation for the whole machine, not
  for one process.
- A 2-bit quant that passes a chat check can fail a JSON tool call. If the job is tool calling, test
  that, not "hello".
