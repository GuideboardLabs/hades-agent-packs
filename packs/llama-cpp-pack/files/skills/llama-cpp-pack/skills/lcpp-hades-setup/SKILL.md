---
name: lcpp-hades-setup
description: "Taking a Hades agent from a GGUF on disk to a tuned, verified model."
version: 1.0.0
license: MIT
tags: [llama-cpp, hades, setup, agent, tuning]
---

# Do this for a Hades user

The job someone actually wants is not "explain llama.cpp". It is: here is a model, make my agent
run it well. That is eight steps, in this order, and each one has a way to go wrong quietly.

Run `tools/lcpp-adapt.py` first. It prints which of this pack's assumptions are false on this
machine and writes a profile the other tools read. If it exits 3, stop and fix. Nothing below is
trustworthy until it exits 0 or 4 with the conflicts understood.

## 1. Identify the artifact

Read the file's header, not its name: `tools/lcpp-gguf.py model.gguf` gives architecture, block
count, expert count, head counts and the training context. Check the magic bytes (`head -c 4` is
`GGUF`) and the size against the repo's manifest. A `sha256` match proves a faithful copy, not a
good source, so check the repo is live and current while you are at it.

## 2. Decide the backend from the silicon

`tools/lcpp-detect.py`, then `tools/lcpp-build.sh`. On AMD the question is Vulkan versus ROCm and
the answer is the GPU's `gfx` target, not the distro. RDNA1 and much of RDNA2: Vulkan. RDNA3, RDNA4,
CDNA: build ROCm too and compare. On NVIDIA: CUDA, and the CUDA toolkit must be new enough for the
card's `sm` target or the build fails on an arch it does not know.

## 3. Fit before you download

`tools/lcpp-recommend.py` ranks what is already on the machine and names the flags. If the model
does not fit, choose a smaller quant of the same model before choosing a smaller model. A 35B at Q4
that fits beats a 12B at Q4 that also fits, on almost every task, at every speed that still clears
the floor.

## 4. Tune on the idle card

`tools/lcpp-tune.py model.gguf`. It refuses to run while another model holds the card, sweeps
`ncmoe` or `ngl`, then threads, then context, and reports the winner with the VRAM it used. Do not
skip the idle requirement to save time: a browser tab rendering at 9 FPS in software cost 4 cores
and halved decode on this machine, and the number that came out described the browser.

## 5. Pre-test before wiring anything

`tools/lcpp-smoke.py --url http://127.0.0.1:PORT`. Five cheap checks that catch the failures which
otherwise surface hours later inside the agent:

- the chat template returns content at all
- the thinking budget leaves room for an answer instead of returning reasoning and nothing else
- tool calls come back with JSON arguments, not a string cut mid-quote
- a prompt at 55 percent of the context still returns a needle planted in it
- decode clears the admission floor with a real generation, not three tokens

A model that fails tool calling is not an agent model, whatever its benchmark says.

## 6. Serve it as a unit, disabled

`tools/lcpp-serve.sh --from-report tune.json --write --start`. It checks the binary's source tree
knows the model's architecture before writing the unit, emits the Vulkan spill variable when the
build is Vulkan, and leaves the unit disabled at boot.

## 7. Point the agent at it

In `~/.hades/config.json`, back the file up first, then set the model alias, the endpoint URL and
`context_tokens` to exactly the served `n_ctx`. A config that claims more context than the server
serves produces an HTTP 400 in the middle of a task, not at startup. If the model is small enough to
share the card, wire vision too: start `hades-vision.service` and export
`HADES_VISION_BIN`, `HADES_VISION_MODEL`, `HADES_VISION_PROJECTOR`, `HADES_VISION_PORT`, or add a
`vision` block with `url`, `model`, `key_env`.

## 8. Verify in the agent, then report

`hades turn "..." --model NAME --max-tokens 64` proves the endpoint, alias and sampler resolve.
Then a real turn with a tool call proves the loop. Report: what was measured, what was assumed, what
is still unverified, and the restore command.

## Restore

Every measurement session ends the same way: stop the bench server, confirm VRAM back to about
90 MB, start what the household was using, leave units disabled. A forgotten 20 GB model is a
silent tax on the next job and the owner will blame the pack.
