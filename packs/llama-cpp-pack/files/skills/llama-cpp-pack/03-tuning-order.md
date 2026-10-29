# The order to tune in

Tuning out of order wastes hours, because a bad fit and a bad thread count both look like "slow
model". This order is the one that produced every number in this pack.

## Step 0: idle the machine, and prove it

```bash
cat /sys/class/drm/card1/device/mem_info_vram_used     # want ~90 MB, not 7300
pgrep -a llama-server                                  # want nothing but the one you are testing
top -bn1 -o %CPU | head -12                            # want no browser near the top
```

A Chrome tab rendering a Gaussian splat scene in llvmpipe at 9 FPS held about four cores. Identical
flags measured 5.12 t/s with the tab open and 9.84 t/s with it closed. On a 6-core box that is the
difference between "fails the 10 t/s floor" and "passes".

Also check swap and page cache: a probe that starts streaming weights from a USB drive while the
kernel evicts pages will report prefill rates that belong to the disk, not the model.

## Step 1: make it fit (weights)

Start with `-ngl 999` and all experts on CPU (`-ncmoe <n_layers>` for MoE, `-ngl <n>` for dense), then
walk toward the card:

| Model | Setting | VRAM | decode |
|---|---|---|---|
| Clover-35B-A3B Q4_0, 40 layers | `-ncmoe 34` | 4.44 GB | 5.12 t/s |
| same | `-ncmoe 26` | 8.05 GB | 10.29 t/s |
| same | `-ncmoe 22` | 8.53 GB (spill) | 2.20 t/s |

The curve is flat, then a cliff. Find the last setting before the cliff, back off one step, and stop.

**Dense models** have no `-ncmoe`; you spend `-ngl`. Gemma4-12B Q4_K_XL at 32k context needs
`-ngl 50` on 8 GB. Below the fit point quality does not degrade gradually, throughput does.

## Step 2: remove page faults

For anything above ~12 GB, compare mmap against `--load-mode none`:

- 18.24 GiB model, mmap plus expert overrides: **48.7 t/s prefill**, 3.26 t/s decode
- same model, `--load-mode none`: **218.6 t/s prefill**, 4.93 t/s decode at a worse offload setting

Prefill improves 4.5x from this alone. Decode improves because the same page faults happen per token,
just fewer of them.

## Step 3: KV cache type, then context

Add `-ctk q8_0 -ctv q8_0 -fa on`, then raise `-c` until VRAM is back at the ceiling.

- Qwen3.8-27B on this card: 32k with q4_0 KV ran 24.2 t/s, 64k dropped to 11.8 t/s, 128k was the
  practical ceiling.
- Groot-19B-A3B hybrid at 32k with q8_0 KV: 22.2 t/s decode, 312 t/s prefill, 7.34 GB. The same model
  at 20,480 with fp16 KV ran 22.3 to 22.5 t/s at 7.40 GB, so q8_0 bought 60 percent more context for
  nothing.
- `-np 1` is part of this step: four default slots would have quartered that context.

## Step 4: threads

Physical cores, never more. Measured on a 6-core/12-thread Zen 1:

| Setting | decode |
|---|---|
| `-t 6 -tb 6` | 9.84 t/s |
| `-t 10 -tb 10` | 2.87 t/s |

The Zen 1 CCX pinning hypothesis was tested and refuted: pinning six threads to one 3-core CCX
measured 28.3 t/s prompt and 4.82 t/s generation against 30.7 and 6.52 unpinned. Keep `--threads` at
physical-core count and let the scheduler spread them.

## Step 5: batch sizes for prefill

`-b 2048 -ub 1024` is the common field setting. Raise `-b` to speed up prompt processing on long
prompts; lower `-ub` when VRAM is tight during prefill. This is the last throughput knob and the
smallest, so do it after weights, KV and threads.

## Step 6: sampling, and what the client overrides

Server flags set defaults for requests that do not specify their own. A harness that posts
`"temperature": 0.3` ignores `--temp 1.0`. Two separate places to check:

```bash
grep -n 'temp' ~/.config/systemd/user/<unit>.service        # server default
curl -s localhost:PORT/slots | head -c 400                   # what the live request actually used
```

## Step 7: speculation, last

Try it and keep it only if it wins on your own acceptance profile. On this card `--spec-type
draft-self` measured 9.84 t/s against 10.29 plain. Where it does win: a card with spare compute and a
draft model or MTP heads that agree with the target. Field reports show +40 percent from MTP on an
R9700 with ROCm, and n-gram drafting pushing code-edit throughput past 500 t/s because the answer
copies long runs straight out of the prompt.

## Step 8: prove the config with a real workload

A short probe at a 900-token prompt says nothing about a 12k-token agent task with 3k of thinking.
Re-measure at the shape you will actually use:

- same prompt length as production
- same generation length (decode drifts down as KV grows)
- same client, because sampling and templates come from the client

Then write down: model file, quant, all flags, VRAM, GTT, prefill, decode at two prompt depths, and the
wall time of a representative request. That record is the thing future you trusts.
