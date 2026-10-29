# Hardware profiles

Numbers are decode unless labelled prefill. Every local number was taken on an idle desktop with the
server's own timing, at the context stated. Treat them as a shape, not a promise: the same model on
the same card moves 5 to 10 percent between builds.

## This machine: RX 5700 XT 8 GB (RDNA1) + Ryzen 5 1600 + 31 GB RAM

RDNA1 has no matrix units, so it behaves like a fast CPU with 8 GB of very fast memory: bandwidth
bound, and the win comes from putting as many weights on the card as fit and no more.

| Model | Flags of note | Context | VRAM | decode | prefill |
|---|---|---|---|---|---|
| Qwen3.8-27B Q3-DOWN-XS (dense 27B) | `-ngl 999`, q4_0 KV | 32k | ~7.8 GB | 24.5 | |
| same | q4_0 KV | 64k | | 11.8 | |
| Qwen3.6-35B-A3B Q4_K_M (MoE) | `-ncmoe 40` | 8k | | 20.5 | |
| Groot-19B-A3B hybrid | `-ncmoe 20 -c 32768 -ctk/-ctv q8_0` | 32k | 7.34 GB | 22.2 | 312 |
| Clover-35B-A3B block-diffusion MoE | `-ncmoe 26 -fa on --load-mode none` | 8k | 8.05 GB | 10.29 | 285 |
| Flint-40B-A?B | `-ncmoe 39` | 16k | 6.69 GB | 18.6 | |
| same | `-ncmoe 37` | 16k | 7.58 GB | 19.0 | |

**RDNA1 specifics worth knowing**

- `GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM=1` is required to keep the Vulkan backend from allocating
  host-visible memory as if it were VRAM. On this box it is part of every production unit file.
- The GTT heap (15.6 GiB on this card) lets you exceed VRAM without an error. Use it deliberately or
  not at all.
- A ternary fork (TQ2_0) reached 56 to 65 t/s on a 27B where the standard build ran ~24. The format
  matters more than the tuning on this card.
- The card is shared with the desktop. Budget 7.6 GB usable, not 8.
- Headless operation wants a forced mode (`1920x1080_60` on DisplayPort-0); a VNC supervisor must not
  pin a display.

**CPU side**: 6 cores, 3+3 CCX topology. `--threads 6` maximum. USB model storage runs 15 to 66 MB/s,
so weights to measure belong on the internal SATA SSD.

## NVIDIA, from field reports (see `08-field-reports.md` for quotes)

| Card | Model | Flags | Context | decode | prefill |
|---|---|---|---|---|---|
| RTX 3060 12 GB + 16 GB RAM | Flint-40B-A2.5B Q3_K_XL | `-ngl 99 --n-cpu-moe 33 -fa on --jinja -np 1 --cache-ram 0 -ctk q8_0 -ctv q8_0 -b 2048 -ub 1024` | 256k | 46 | 675 |
| RTX 3060 12 GB | Qwen3.6-35B-A3B UD-Q4_K_XL + vision | `--n-cpu-moe 26 -fa on -np 1 -ctk/-ctv q8_0 -b 2048 -ub 1024 --no-mmproj-offload` | 150k | 45 | |
| RTX 3060 12 GB | Bonsai 2 27B PQ2_0 (7.21 GB) | `-ngl 99` | 220k | 35 | 550 |
| RTX 3060 12 GB | LFM3-1.7B | `-ngl 99` (3.8 GB resident) | 256k | 244 | 9000 |
| RTX 3090 24 GB | Laguna S-2.1 IQ3_S, 118B MoE ~8B active | `-ngl 999 --n-cpu-moe 32 -fa on`, f16 KV | 64k | 30 to 35 | 280 to 290 |
| RTX 4090 24 GB | Qwen3.8-27B dense with MTP | MTP heads | 260k | 65 | |
| Tesla T4 16 GB (2018, Colab) | Gemma 4 26B-A4B QAT Q4_K_XL | `-ngl 99 -c 250000` | 35k | 47.4 | 788 |
| same | | | 80k | 20.4 | 490 |
| same | | | 180k | 11.1 | 242 |
| same | | | 250k | 8.9 | 220 |

The T4 row is the cleanest public demonstration of the cost of context: same weights, same flags,
decode falls 5.3x from 35k to 250k while prefill falls 3.6x.

## AMD beyond RDNA1, from field reports

| Card | Model | Notes | decode (chat) | decode (code edits) |
|---|---|---|---|---|
| Radeon R9700 32 GB, ROCm | Qwen3.6-35B-A3B | stock build | 65 | |
| same | | rdna-boosts build | 98 | |
| same | | plus MTP draft heads | 139 | 535 |
| same | | plus n-gram drafting | 143 | 535 |
| RX 7900 XTX 24 GB, ROCm | Qwen3.6-35B-A3B | | 133 | 397 |
| R9700 32 GB | Qwen3.8-27B dense | | 72 | 288 |
| 7900 XTX 24 GB | Qwen3.8-27B dense | | 77 | 262 |

Two lessons that transfer: a fork build was worth +50 percent on ROCm (`rdna-boosts`), and drafting
turned a 143 t/s chat rate into 535 t/s on code edits because the answers copy long spans out of the
prompt. On AMD, the build and the draft matter as much as the flags.

## Apple silicon

Field reports put llama.cpp at 22 t/s on Qwen3.5-9B on an M5 MacBook Pro against MLX at 25 and a
specialized engine at 92. Treat llama.cpp as the compatibility choice on Mac, not the fast one, unless
you have measured your own Metal build.

## CPU only

Workable for 3B to 9B at a few tokens per second, and for the vision projector when you use
`--no-mmproj-offload`. Not workable for a 30B-class model if you want an answer today. On this box, a
9B Q4_K_M at `-ngl 0 -t 6` measured 6.5 t/s generation and 30.7 t/s prompt.
