# Flags that matter

Grouped by the question they answer. Defaults are from llama.cpp master as of 2026-10; forks drift,
so `llama-server --help | grep <flag>` is the authority for the build in your hand.

## Does it fit

**`-ngl N` / `--gpu-layers N`**
Layers whose weights go to the GPU. `999` means all of them. Dense models larger than VRAM need a
real number here (Gemma4-12B Q4_K_XL needs `-ngl 50` on 8 GB). MoE models want `-ngl 999` plus
expert offload instead, because attention layers are cheap to place and experts are not.

**`--n-cpu-moe N` / `-ncmoe N`**
Keep the experts of N layers in system RAM. This is the flag that makes a 35B to 118B MoE fit on a
consumer card. Semantics are per layer, so sweeps are short and the optimum is sharp: on an 18.24 GiB
Q4_0 40-layer MoE, `-ncmoe 26` gave 10.29 t/s at 8.05 GB VRAM, `-ncmoe 34` gave 5.12, and `-ncmoe 22`
hit the ceiling at 8.53 GB and collapsed to 2.20. Sweep in steps of four, then bisect.

**`-fa on` / `--flash-attn`**
Required for cheap long context and for KV quantization to work well. On RDNA1 it is the default in
recent builds; state it explicitly in unit files so a future default flip does not change behavior.

**`--no-mmproj-offload`**
Runs the vision projector on the CPU. On a 12 GB card this is what lets a 35B with vision coexist
with its own weights for roughly zero VRAM (field report: RTX 3060, Qwen3.6-35B-A3B, 150K context).

**`--load-mode none` / `--no-mmap`**
Loads the model fully into RAM instead of mapping the file. Costs load time and RAM, buys predictable
prefill. Measured on an 18.24 GiB model: 48.7 t/s prefill with mmap and expert overrides, 218.6 t/s
without. Use it whenever `tensor overrides` are in play, because page faults on overridden tensors
land inside prompt processing.

**`--cache-ram MB`**
Host-side prompt cache budget. `0` disables it. Field configs on small cards use `--cache-ram 0` so
the RAM budget goes to the model instead.

**`-np N` / `--parallel N`**
Server slots. **Default is 4 in many builds, and each slot carries its own KV cache**, which is the
single most common reason a context that "should fit" OOMs. Force `-np 1` for a single-user server.

**`--no-kv-offload`**
Keeps the KV cache in system RAM. Useful when weights nearly fill the card and context is short,
rarely what you want for interactive work.

## Context and KV cache

**`-c N`**
Total context across slots, not per slot. With `-np 4`, a 32k `-c` gives 8k per slot.

**`-ctk q8_0 -ctv q8_0` / `--cache-type-k` `--cache-type-v`**
Quantized KV cache. q8_0 is the safe default, q4_0 is aggressive. Hybrid models (gated delta net,
linear attention) hold far less KV per token than full attention models, so the same cache type buys
much more context: a 40-layer hybrid at 32k with q8_0 KV used 7.34 GB total and cost nothing versus
fp16 KV (22.2 vs 22.3 t/s measured).

**`--context-shift` / `--no-context-shift`**
Whether the server slides the window when the conversation outgrows `-c`. Shifting discards the
oldest tokens. If the caller has its own trim policy, disable shifting so the failure is explicit
rather than silent amnesia.

## Throughput

**`-t N` / `--threads`**, **`-tb N` / `--threads-batch`**
Prompt and generation thread counts. Keep at or below physical cores. On a 6-core Zen 1, `-t 10`
measured 2.87 t/s where `-t 6` measured 9.84. Pinning all threads to one CCX was tested and refuted
on this box: threads per physical core dominate the Infinity Fabric crossing saved.

**`-b N` / `--batch-size`**, **`-ub N` / `--ubatch-size`**
Prompt processing batch and physical micro-batch. 2048/1024 is the common field setting for big
contexts; smaller ubatch lowers peak VRAM during prefill, which matters when the weights already sit
at the ceiling.

**`--prio N`**
Process priority. **Requires privileges on this box** and otherwise floods the log with one
`failed to set thread priority` line per thread, tens of thousands of lines per run. Omit it.

**`--cpu-mask` / `--cpu-strict`**
Thread affinity. Leave alone unless you have measured a topology problem.

**`--no-warmup`**
Skips the warmup inference. Fine for a server that will be measured after the first real request,
saves a few seconds of load.

## Sampling and reasoning

**`--temp`, `--top-k`, `--top-p`, `--min-p`, `--repeat-penalty`**
Server-side defaults for requests that do not set their own. Beware: an HTTP client that sends
`temperature` explicitly overrides these, so a harness pinning `0.3` will silently ignore your
`--temp 1.0`. Check the request body, not the unit file.

**`--jinja`**
Use the model's own chat template from the GGUF. Required for anything with tool calling or
reasoning sections. Without it a Qwen-style model still answers but loses tool grammar.

**`--reasoning off --reasoning-format none`**
For architectures (DeepSeek-V4, R1 distills) that emit `reasoning_content` and leave `content` empty.
`--reasoning off` alone does not move the text into `content`; both flags are needed or the client
receives an empty answer with a nonzero token count.

**Thinking budgets**
A model with a reasoning section spends tokens before it answers. At a 900-token cap one model
returned 3,731 characters of reasoning, zero characters of content, and `finish_reason: length`. At
3,000 tokens it answered correctly in 936 characters. Treat anything under ~1,500 as a trap.

## Speculative decoding

**`--spec-type draft-self`** (block-diffusion / draft-self builds, e.g. PR 31306)
Self-speculation from the target model's own heads. Measured here: 9.84 t/s against 10.29 t/s plain,
a 4 percent loss at low acceptance. Pays on hardware with spare compute and high acceptance; does
not pay on a bandwidth-starved card.

**`-md <draft.gguf>` with `--draft-max`, `--draft-min`, `--draft-p-min`**
Separate draft model. Worth it when the draft is a small sibling of the same family and the task is
repetitive (code edits, summarization). Field reports put MTP draft heads at +40 percent on an R9700,
and n-gram drafting above 500 t/s on code edits where the answer copies long runs from the prompt.

## Server surface

**`--host 127.0.0.1 --port N`**, **`--alias NAME`**
Bind locally, name the model so clients do not need the path. `--alias` is what makes `/v1/models`
readable and what an OpenAI-compatible client sends.

**`--timeout S`**
Per-request server timeout. **The default (~2 minutes) kills long generations on slow hardware.** A
coding task at 10 t/s needs `--timeout 1800`. Symptom when it fires:
`slot release: n_tokens = ..., truncated = 1` plus `W srv next: stopping wait for next result`.

**`--slots`, `--metrics`**
Expose in-flight slot state and Prometheus metrics. `GET /slots` is the cheapest way to prove a
server is working rather than hung: it reports `n_prompt_tokens` and `n_prompt_tokens_processed` for
the active request.

**`-cb` / `--cont-batching`**
Continuous batching across slots. Irrelevant with `-np 1`, useful for multi-user servers.
