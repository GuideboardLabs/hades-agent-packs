---
name: lcpp-build-backend
description: "Building llama.cpp for the right backend on this Linux box."
version: 1.0.0
license: MIT
tags: [llama-cpp, build, vulkan, rocm, cuda]
---

# Build llama.cpp for this machine

```bash
tools/lcpp-build.sh --dry-run              # see the exact cmake line first
tools/lcpp-build.sh                        # auto backend, build, verify
tools/lcpp-build.sh --backend vulkan       # force one: vulkan | rocm | cuda | cpu
tools/lcpp-build.sh --dir ~/llama.cpp --jobs 6
```

## Choosing the backend is a support question, not an install question

- **NVIDIA**: CUDA when `nvcc` exists. Blackwell parts need a CUDA 12.8 or newer toolchain; an
  older one fails on the arch and the error names `sm_120`.
- **AMD, RDNA3/4 or CDNA** (`gfx1100`, `gfx1101`, `gfx1102`, `gfx1200`, `gfx90a`, `gfx942`): ROCm
  is worth the trouble. Field reports on a 32 GB R9700 measured 65 t/s stock and 98 t/s from a
  tuned ROCm build, so the build can be worth more than the flags.
- **AMD, RDNA1** (`gfx1010`, `gfx1011`, `gfx1012`) and much of RDNA2 consumer: current ROCm does
  not build kernels for these. Vulkan works and is the correct choice. The script reads
  `rocminfo`, sees `gfx1010`, and refuses to pick ROCm with that reason printed.
- **Intel**: Vulkan, or the CPU build. OneAPI paths exist and are not what this pack tests.
- **No GPU**: `-DGGML_NATIVE=ON -DGGML_OPENMP=ON`.

## Flags that are not optional

```
-DCMAKE_BUILD_TYPE=Release
-DLLAMA_CURL=OFF            # a build that cannot fetch models cannot leak a request either
-DLLAMA_BUILD_TESTS=OFF     # minutes of build time for a server you will not run
```

## Build inside a memory scope

An agent shell frequently has a 4 GB ceiling, and a `-j6` C++ build dies there with exit 143 that
looks like a compiler bug. The script wraps the build in
`systemd-run --user --scope -p MemoryMax=22G` when available; set `LCPP_MEMORY_CAP` to change the
number or `LCPP_NO_SCOPE=1` to opt out.

## Verify, do not assume

The script prints three facts after the build:

- `llama-server --version` (there is a real binary, not a stale one)
- `--mmproj` in `--help` (this build can do vision)
- `n-cpu-moe` in `--help` (this build can offload MoE experts)

If `--mmproj` is missing, vision is a text-only illusion. If `n-cpu-moe` is missing, a 35B MoE
cannot be made to fit a small card with that binary, whatever the flags.

## Architectures and forks

A build only runs the architectures its source tree knows. Before serving a new model, check the
symbol: `grep -r LLM_ARCH_"$(echo $ARCH | tr a-z A-Z)" src/llama-arch.cpp`. Models from a launch
week often need their own PR (`--spec-type draft-self` arrived with PR 31306 for one model; another
family needed 30928). `tools/lcpp-serve.sh` does this check and switches binaries when it can.
