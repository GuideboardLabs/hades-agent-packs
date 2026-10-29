# Field reports from X

Harvested 2026-10-29 with a logged-in browser profile, searching for practitioners posting configs and
numbers rather than benchmarks of datacenter hardware. Quotes are verbatim, links are permalinks.
Selection rule: the post had to state a GPU, a model file, and at least one number or flag.

The dominant voice in this space is **@DogukanUrker**, who posts "running X on a single RTX 3060"
threads with the full `llama-server` line. His setups are the closest published analogue to the
problem in this pack: a small card, a large MoE, and a flag list that makes it work.

---

## RTX 3060 12 GB + 16 GB RAM, Flint-40B-A2.5B, 256k context

> running Recursive Flint 40B-A2.5B on a single RTX 3060. 12GB VRAM, 16GB RAM. (config below) 256K
> context. ~46 tok/s decode, ~675 tok/s prefill. ... only 14 of 49 layers are full attention, so 256K
> ctx costs just 3.8GB of q8 kv. ... llama.cpp support isn't merged yet, so this runs on PR #30928
> cherry-picked onto master.
>
> llama-server -m flint-preview-UD-Q3_K_XL.gguf -ngl 99 --n-cpu-moe 33 -c 262144 -fa on --jinja -np 1
> --cache-ram 0 --cache-type-k q8_0 --cache-type-v q8_0 -b 2048 -ub 1024 --temp 1.0 --top-p 0.95
> --top-k 20 --min-p 0.0 --repeat-penalty 1.0

https://x.com/DogukanUrker/status/2113060329038970892

What transfers: the whole flag set is the recipe in `01-flags.md`, in the order it is described there.
256k on 12 GB only works because 35 of 49 layers are hybrid attention (little KV) and because
`--n-cpu-moe 33` moves most expert weights to RAM. What does not transfer to an 8 GB card: `-ncmoe 33`
of 49 layers leaves around 16 layers of experts on a 12 GB card, roughly 4 GB of experts plus
attention. Halve the card and you have to give up context or layers.

## RTX 3060 12 GB, Qwen3.6-35B-A3B with vision, 150k context

> the trick is one flag nobody talks about: -np 1 llama.cpp defaults n_parallel to 4, which quietly
> 4x's your KV cache and OOMs you. force a single slot and 150K context fits on a 12GB card. the
> load-bearing bits: --n-cpu-moe 26 pushes MoE experts into system RAM, q8_0 KV cache halves the
> memory, and --no-mmproj-offload runs the vision projector on CPU so you get a 35B with vision for
> ~0 extra VRAM.
>
> llama-server -m Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf --mmproj mmproj-qwen3.6-35b-a3b-F16.gguf
> --no-mmproj-offload -ngl 99 --n-cpu-moe 26 -c 150000 -fa on -np 1 --cache-type-k q8_0
> --cache-type-v q8_0 -b 2048 -ub 1024

https://x.com/DogukanUrker/status/2077472690156511643

And on which flag is load-bearing, replying to a reader who credited the MoE offload alone:

> both are doing work. --n-cpu-moe 26 is what fits the weights (experts to RAM, frees the VRAM). -np 1
> is what fits the KV cache at 150k (default n_parallel=4 quadruples it). take either one out and you
> OOM, just for different reasons.

The `--no-mmproj-offload` detail is the one worth stealing for `10-vision.md`: vision costs about zero
VRAM if you let the projector run on the CPU.

## RTX 3060 12 GB, Bonsai 2 27B ternary, 220k context

> running PrismML's Bonsai 2 27B on a single RTX 3060. 12GB VRAM. (config below) 220K context, ~35
> tok/s decode, ~550 tok/s prefill. a 27B at 1.72 bits/weight, real ternary. two packings: PQ2_0
> (7.21 GB) gets 220K and the ...

https://x.com/DogukanUrker/status/2100692816846573608

A 27B at 1.72 bits in 7.21 GB, 220k context, 35 t/s on a 12 GB card. This is the format argument in
`06-quants.md` with numbers attached.

## RTX 3060 12 GB, LFM3-1.7B, 256k context

> ~244 tok/s decode, ~9K tok/s prefill. full 256K fits in 3.8GB of VRAM. still 53 tok/s with 252K
> tokens in it. there's room for 4 agents with ...

https://x.com/DogukanUrker/status/2115163944788566445

Useful as an upper bound on what this class of prompt looks like when weights are not the constraint.

## RTX 3090 24 GB, Laguna S-2.1 IQ3_S, 118B MoE, 64k

> ~30-35t/s decode ~280-290t/s prefill ~23GB VRAM @ 64k context, f16 KV llama.cpp: -ngl 999 +
> --n-cpu-moe 32 + fa on. 118B MoE, ~8B active, did we get a decent mid-range model that doesn't need
> multi gpu and can run with good enough speeds for long-context coding tasks?

https://x.com/ItsmeAjayKV/status/2080007322630578288

The reply thread is worth reading for calibration: a reader called 280 t/s prefill "completely
unusable" for long-context coding, and the author answered that his bar was low because other models
in the class were worse. Prefill rate is a UX threshold specific to the task, and 118B of weights on
one 24 GB card buys context at the price of prompt speed.

## Tesla T4 16 GB, Gemma 4 26B-A4B QAT, context sweep

> - 35k context: [ Prompt: 788.8 t/s | Gen: 47.4 t/s ] (-ngl 99)
> - 80k context: [prefill: 490.8 t/s | decode: 20.4 t/s ]
> - 180k context: [ Prompt: 242.4 t/s | Gen: 11.1 t/s ]
> - 250k context: [ Prompt: 220.2 t/s | Gen: 8.9 t/s ]
> ./build/bin/llama-cli -m gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf ... -n 24000 -ngl 99 -c 250000

https://x.com/analogalok/status/2072650920697934132

A 2018 datacenter card with 16 GB, no tuning beyond `-ngl 99`, and the clearest public measurement of
context cost: 5.3x decode loss from 35k to 250k. Note it is a `llama-cli` run, so prefill rates include
no server overhead.

## AMD with ROCm: R9700 32 GB and 7900 XTX 24 GB

> did some tests w/ one GPU per model, llama.cpp on ROCm, no splitting.
> Qwen3.6-35B-A3B (MoE) - R9700 32GB: 143 tok/s chat, 535 on code edits - 7900 XTX 24GB: 133 chat
> tok/s, 397 code edits. Qwen3.8-27B (dense) - R9700: 72 toks/s chat, 288 code edits - 7900 XTX: 77
> toks/s chat, 262 code edits. Starting point on the R9700 with the stock build and no drafting: 65
> tok/s on the 35B - 28 on the 27B. how I got there: 1. rdna-boosts llama.cpp build. 65 to 98 on its
> own. 2. MTP: the model's own prediction heads draft 3 tokens ahead. 98 to 139. 3. n-gram drafting on
> top, which copies runs straight from the prompt. That's why code edits go past 500. 4. on the 27B,
> a DFlash draft model beat MTP for code.

https://x.com/duycaoxyz/status/2112483249121538470

The most useful AMD datapoint in the set: 32 GB of RDNA4 more than doubles this machine's bandwidth
budget and lands at 65 t/s stock, then 2.2x from a fork plus drafting. Two transferable conclusions:
the build can be worth more than the flags, and drafting pays hugely when the workload copies text
from the prompt (code edits) and barely at all for open-ended chat.

## RTX 4090 24 GB, dense 27B with MTP, 260k

> Qwen 3.8 27B (dense) running on a single RTX 4090 (24GB VRAM) at 65 tokens/sec decode with MTP!
> 260,000 context window or 65 tokens/sec decode with native MTP.

https://x.com/analogalok/status/2088326480669667699

A dense 27B holding 260k and 65 t/s on 24 GB is MTP plus a very large card, not a tuning trick. Useful
as an upper bound for what the same model class does when it fits natively.

## Apple silicon, as a cross-check

> I ran Qwen3.5 9B on a base M5 MacBook Pro using three local inference engines, one at a time.
> llama.cpp reached 22.0 tok/s, MLX 25.1, a newer engine 92.1.

https://x.com/_avichawla/status/2115010493899502058

On unified memory, llama.cpp is the portability choice and not the fast path.

---

## What the recon says overall

1. **`-np 1` is the most under-reported flag.** Default parallelism silently multiplies KV and OOMs
   long context. It appears in the two best small-card configs here and in none of the marketing.
2. **`--n-cpu-moe` is standard practice for MoE on a consumer card.** Every serious small-card config
   in this set uses it, with the value set so that attention stays on the GPU and experts mostly do
   not.
3. **KV quantization to q8_0 is universal** among people running long context on 12 to 24 GB.
4. **Prefill rate is the hidden cost of context.** The T4 sweep and the 3090 thread both show it: 250k
   context turns a 788 t/s prompt into a 220 t/s prompt, and some users find 280 t/s unusable.
5. **Build choice and drafting can beat flag tuning.** ROCm users report +50 percent from a fork and
   +40 percent from MTP, which is more than any sampler or batch setting will give you.
6. **Nobody is publishing Vulkan numbers for RDNA1.** The AMD conversation on X is ROCm on RDNA3/4.
   The RDNA1 measurements in `05-hardware.md` are local and unpublished elsewhere, which is why they
   are recorded in detail.

## Gaps, stated honestly

- No verified RTX 50-series configuration surfaced in these searches. Marketing posts mention the
  cards; none gave flags and numbers.
- No Vulkan-versus-ROCm comparison for the same card and model was found.
- Apple numbers are single-anecdote.
- Every decode figure here is the author's own report, mostly from `llama-cli` or a server log, and
  none come with a machine-idle guarantee. Treat them as shape, not as targets.

## How this recon was done

`~/.hermes/x-cloak/scripts/search-x.py` (new, mirrors `visit-tweet.py`): logs into the persistent
profile, opens `x.com/search?q=...&f=top`, accumulates tweets keyed by status id while scrolling so a
virtualized re-render cannot drop or duplicate one, and writes JSON. `visit-tweet.py` then pulled full
threads for the posts worth reading. Queries that worked: `"llama.cpp" "tokens/s" min_faves:25`,
`"llama.cpp" "-ngl" min_faves:8`, `"llama.cpp" "n-cpu-moe" min_faves:3`, `"llama.cpp" "7900 XTX"
tokens`, `"llama.cpp" "5070 Ti" tokens`, `"llama.cpp" "5090" tokens/s`. Queries with bare product
names return content marketing; quoted flags and `min_faves` return practitioners.
