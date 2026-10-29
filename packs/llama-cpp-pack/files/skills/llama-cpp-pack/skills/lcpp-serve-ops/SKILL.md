---
name: lcpp-serve-ops
description: "Turning a measured config into a systemd unit, and keeping it honest."
version: 1.0.0
license: MIT
tags: [llama-cpp, systemd, ops, serving]
---

# Serve it as a unit

```bash
tools/lcpp-serve.sh --model ~/models/m.gguf --alias m-35b --port 8115 \
  --flags "-ngl 999 -ncmoe 26 -fa on -c 8192 -ctk q8_0 -ctv q8_0"
tools/lcpp-serve.sh --from-report tune.json --write --start
```

The script prints the unit, checks that the binary's source tree knows the model's architecture
(switching to a build that does if one exists), emits the RDNA1 Vulkan environment line when the
binary path says vulkan, and leaves the unit **disabled**.

## Why disabled

A model that starts at boot holds the card before anyone decided to use it. Every model unit on a
well-run box is started deliberately and enabled only if something depends on it existing.

## The unit lines that matter

- `Environment=GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM=1` on Vulkan AMD. Without it the backend treats
  host memory as VRAM and the first long prompt falls off a cliff. The variable name has a typo in
  it; copy it exactly.
- `--parallel 1` / `-np 1`. Default 4 slots each carry KV, which is why contexts that "should fit"
  do not.
- `--timeout 1800`. The default two minutes kills a long generation mid-answer.
- `Restart=on-failure` with `RestartSec=3`, and `MemoryMax=90%` so a runaway load cannot take the
  machine with it.

## Health and identity

```bash
curl -s -o /dev/null -w '%{http_code}\n' localhost:8115/health       # 200 loaded, 503 loading
curl -s localhost:8115/props | jq -r .model_path                      # which weights, really
curl -s localhost:8115/slots | jq '.[0] | {is_processing, n_prompt_tokens, n_prompt_tokens_processed}'
```

`/health` says something is listening; `/props` says what. On a box with more than one model, the
second question is the one that matters.

## Restore discipline

After a measurement: stop the bench server, confirm VRAM back to about 90 MB, start whatever the
household was using, leave units disabled. A forgotten 20 GB model is a silent tax on the next job.
