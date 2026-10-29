#!/usr/bin/env bash
# Build llama.cpp for this machine's best backend, then prove the binary works.
#
#   ./lcpp-build.sh                    auto-detect the backend and build
#   ./lcpp-build.sh --backend rocm     force one: vulkan | rocm | cuda | cpu
#   ./lcpp-build.sh --dir ~/llama.cpp  choose the source tree (cloned if missing)
#   ./lcpp-build.sh --dry-run          print the cmake line and exit
#
# Why a script: the backend choice and five cmake flags are the difference between a build that
# uses the card and one that quietly runs everything on the CPU, and the verification at the end
# is the part people skip.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="${LCPP_BACKEND:-auto}"
DIR="${LCPP_DIR:-$HOME/llama.cpp}"
JOBS="$(nproc)"
DRY_RUN=0
MEMORY_CAP="${LCPP_MEMORY_CAP:-22G}"

while [ $# -gt 0 ]; do
  case "$1" in
    --backend) BACKEND="$2"; shift 2 ;;
    --dir)     DIR="$2"; shift 2 ;;
    --jobs)    JOBS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

PYTHON="${PYTHON:-python3}"

if [ "$BACKEND" = "auto" ]; then
  DETECT="$("$PYTHON" "$HERE/lcpp-detect.py" --json)"
  # The choice is not "which SDK is installed" but "which SDK supports this silicon".
  # ROCm is faster where it works (RDNA3/4, CDNA) and absent or broken on RDNA1 (gfx1010-1012),
  # where a HIP build either fails to compile kernels or silently runs on the CPU. Vulkan works
  # on every AMD generation, so it is the safe default and ROCm has to earn the pick.
  read -r BACKEND REASON <<<"$(printf '%s' "$DETECT" | "$PYTHON" -c '
import json, subprocess, sys
report = json.load(sys.stdin)
gpus = report.get("gpus") or []
backends = report.get("backends", {})
vendor = (gpus[0].get("vendor") if gpus else "") or ""
rocm = backends.get("rocm", {}).get("available")
vulkan = backends.get("vulkan", {}).get("available")
supported = {"gfx900","gfx906","gfx908","gfx90a","gfx942","gfx1030","gfx1100","gfx1101","gfx1102","gfx1200","gfx1201"}
gfx = ""
if rocm:
    try:
        out = subprocess.run("rocminfo 2>/dev/null | grep -o \"gfx[0-9a-f]*\" | head -1", shell=True,
                             capture_output=True, text=True, timeout=20).stdout.strip()
        gfx = out
    except Exception:
        gfx = ""
if vendor == "NVIDIA":
    print("cuda", "nvidia-smi answered")
elif vendor == "AMD" and rocm and (not gfx or gfx in supported):
    print("rocm", "rocm present, target " + (gfx or "unknown"))
elif gpus and (vulkan or vendor in ("AMD", "Intel")):
    print("vulkan", "portable path; ROCm " + ("does not list " + gfx if gfx else "not confirmed for this chip"))
elif gpus and vendor == "NVIDIA":
    print("cuda", "vendor is NVIDIA")
else:
    print("cpu", "no usable GPU backend found")
')"
  echo "backend:  $BACKEND  ($REASON)"
fi

case "$BACKEND" in
  vulkan) FLAGS=(-DGGML_VULKAN=ON) ;;
  rocm)   FLAGS=(-DGGML_HIP=ON -DCMAKE_C_COMPILER=hipcc -DCMAKE_CXX_COMPILER=hipcc) ;;
  cuda)   FLAGS=(-DGGML_CUDA=ON) ;;
  cpu)    FLAGS=(-DGGML_NATIVE=ON -DGGML_OPENMP=ON) ;;
  *) echo "unknown backend: $BACKEND" >&2; exit 2 ;;
esac

BUILD="$DIR/build-$BACKEND"
CMAKE=(cmake -S "$DIR" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
       "${FLAGS[@]}" -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF)
BUILD_CMD=(cmake --build "$BUILD" --config Release --target llama-server llama-cli llama-bench -j "$JOBS")

echo "source:  $DIR"
echo "build:   $BUILD"
echo "cmake:   ${CMAKE[*]}"
if [ "$DRY_RUN" = "1" ]; then
  echo "build:   ${BUILD_CMD[*]}"
  exit 0
fi

if [ ! -d "$DIR/.git" ] && [ ! -f "$DIR/CMakeLists.txt" ]; then
  echo "cloning llama.cpp into $DIR"
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$DIR"
fi

# Build inside a memory-bounded scope when systemd-run is available: an agent's shell often has a
# 4 GB ceiling, and a -j6 C++ build gets OOM-killed there with no useful message.
RUNNER=()
if command -v systemd-run >/dev/null 2>&1 && [ -z "${LCPP_NO_SCOPE:-}" ]; then
  RUNNER=(systemd-run --user --scope -q -p "MemoryMax=$MEMORY_CAP" --)
fi

"${RUNNER[@]}" "${CMAKE[@]}"
"${RUNNER[@]}" "${BUILD_CMD[@]}"

echo
echo "--- verification ---"
"$BUILD/bin/llama-server" --version | head -3 || true
if "$BUILD/bin/llama-server" --help 2>&1 | grep -q -- '--mmproj'; then
  echo "mmproj:   supported (vision is possible with this build)"
else
  echo "mmproj:   NOT supported (text only)"
fi
if "$BUILD/bin/llama-server" --help 2>&1 | grep -q -- 'n-cpu-moe'; then
  echo "ncmoe:    supported (MoE expert offload is possible)"
fi
echo "binaries: $BUILD/bin/{llama-server,llama-cli,llama-bench}"
echo
echo "Next: $HERE/lcpp-recommend.py --scan <model dir>   then   $HERE/lcpp-tune.py <model.gguf>"
