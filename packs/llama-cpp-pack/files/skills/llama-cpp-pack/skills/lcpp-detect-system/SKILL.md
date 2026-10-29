---
name: lcpp-detect-system
description: "Probing a Linux box for llama.cpp: CPU, GPU, VRAM, backends, disks."
version: 1.0.0
license: MIT
tags: [llama-cpp, detection, linux, hardware]
---

# Detect what this machine can do

Run `tools/lcpp-detect.py` before recommending anything. It reads sysfs, procfs, lspci,
nvidia-smi and the version banners; it does not infer capability from the distro name.

```bash
tools/lcpp-detect.py            # human summary
tools/lcpp-detect.py --json     # what the other tools eat
```

## What it reports, and why each field matters

- **CPU physical cores, not logical.** `--threads` above physical cores measured 2.87 t/s where
  physical count measured 9.84 on a 6c/12t Zen 1.
- **ISA flags.** `avx2` is the floor for a fast CPU build; `avx512f`/`avx512_vnni` matters on Xeons
  and newer EPYCs, and `amx_int8` matters a great deal on Sapphire Rapids and later. A CPU build
  without `-DGGML_NATIVE=ON` on a machine whose target is older than the hardware leaves
  performance on the table.
- **GPU vendor, VRAM, driver, PCI id.** AMD exposes `mem_info_vram_total`; NVIDIA needs
  `nvidia-smi`; Intel's iGPU shares system memory and should be treated as a CPU with a fast path.
- **GTT** (`mem_info_gtt_total`): the AMD heap that lets a model exceed VRAM without an error.
  Useful when deliberate, poisonous when accidental.
- **Backends.** Vulkan via ICD json files, ROCm via `/opt/rocm` and `hipcc`, CUDA via `nvcc`.
  Presence is not support: ROCm can be installed on a chip it does not build kernels for.
- **Disks with rotational flag.** Weights to *run* belong on the fastest local disk. A USB model
  drive at 15 to 66 MB/s turns a 6 second prefill into minutes.
- **Existing builds.** A machine that already has a `llama-server` should not build another one
  before checking whether it knows the model's architecture.

## Reading the output critically

- Two GPUs of different sizes: tune the larger one and state which. A model split across two cards
  is a different, usually worse, experiment.
- No GPU: `--ngl 0`, small models, and the CPU build. Do not pretend a 30B is usable.
- iGPU only: Vulkan may work; VRAM is system RAM and the CPU is also doing the work, so expect a
  few tokens per second on anything above 3B.
- A card already loaded with a model: that is where measurements go wrong. `lcpp-tune.py` refuses
  to run until VRAM is free, which is the correct behavior.

## Output contract

`--json` is stable and consumed by `lcpp-recommend.py`, `lcpp-tune.py` and `lcpp-build.sh`:
`gpus[]` with `vendor`, `name`, `vram_gib`, `gtt_gib`, `driver`, `pci`; `backends` with an
`available` flag per backend; `cpu.physical`; `memory.ram_gib`; `disks[]`; `existing_builds[]`.
Add fields rather than renaming them.
