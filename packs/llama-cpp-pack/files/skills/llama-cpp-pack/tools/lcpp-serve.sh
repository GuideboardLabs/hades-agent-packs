#!/usr/bin/env bash
# Turn a measured configuration into a systemd user unit, and prove it starts.
#
#   ./lcpp-serve.sh --model ~/models/m.gguf --alias m-35b --port 8115 \
#                   --flags "-ngl 999 -ncmoe 26 -fa on -c 8192 -ctk q8_0 -ctv q8_0"
#
#   ./lcpp-serve.sh --from-report tune.json      take the winner out of lcpp-tune.py's report
#   ./lcpp-serve.sh ... --write                  install it under ~/.config/systemd/user
#   ./lcpp-serve.sh ... --write --start          install and start, then poll /health
#
# Units are left DISABLED on purpose: a model that starts at login takes the card from whoever
# needed it. Starting it is a decision, so this script makes you take it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
MODEL=""
ALIAS=""
PORT=""
FLAGS=""
UNIT=""
BINARY=""
FROM_REPORT=""
WRITE=0
START=0
CTX_SECONDS=1800

while [ $# -gt 0 ]; do
  case "$1" in
    --model)  MODEL="$2"; shift 2 ;;
    --alias)  ALIAS="$2"; shift 2 ;;
    --port)   PORT="$2"; shift 2 ;;
    --flags)  FLAGS="$2"; shift 2 ;;
    --unit)   UNIT="$2"; shift 2 ;;
    --binary) BINARY="$2"; shift 2 ;;
    --from-report) FROM_REPORT="$2"; shift 2 ;;
    --write)  WRITE=1; shift ;;
    --start)  START=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -n "$FROM_REPORT" ]; then
  eval "$("$PYTHON" - "$FROM_REPORT" <<'PY'
import json, shlex, sys
report = json.load(open(sys.argv[1]))
winner = report["winner"]
print("MODEL=" + shlex.quote(report["model"]))
print("FLAGS=" + shlex.quote(winner["flags"]))
print("ALIAS=" + shlex.quote(report.get("alias") or ""))
PY
)"
fi

if [ -z "$MODEL" ] || [ -z "$FLAGS" ]; then
  echo "--model and --flags (or --from-report) are required" >&2
  exit 2
fi

if [ -z "$BINARY" ]; then
  BINARY="$("$PYTHON" "$HERE/lcpp-detect.py" --json | "$PYTHON" -c 'import json,sys; b=json.load(sys.stdin).get("existing_builds") or []; print(b[0] if b else "")')"
fi
if [ -z "$BINARY" ]; then
  echo "no llama-server found; pass --binary" >&2
  exit 2
fi

# A build only runs the architectures its source tree knows. A fork for some other model is the
# wrong server for this one, and the failure mode ("unknown model architecture") arrives minutes
# later after the weights load. Check the source tree for the arch symbol and prefer a build that
# has it, saying so.
ARCH="$("$PYTHON" "$HERE/lcpp-gguf.py" --json "$MODEL" 2>/dev/null | "$PYTHON" -c 'import json,sys
try: print(json.load(sys.stdin).get("architecture") or "")
except Exception: print("")' || true)"
if [ -n "$ARCH" ]; then
  UPPER="$(printf '%s' "$ARCH" | tr '[:lower:]' '[:upper:]')"
  supports() {
    local src
    src="$(cd "$(dirname "$1")/../.." 2>/dev/null && pwd || echo "")"
    [ -n "$src" ] && grep -rqs "LLM_ARCH_${UPPER}\b" "$src/src/llama-arch.h" "$src/src/llama-arch.cpp" 2>/dev/null
  }
  if ! supports "$BINARY"; then
    while read -r candidate; do
      [ -z "$candidate" ] && continue
      if supports "$candidate"; then
        echo "switching binary: $BINARY does not know arch '$ARCH'; using $candidate"
        BINARY="$candidate"
        break
      fi
    done < <("$PYTHON" "$HERE/lcpp-detect.py" --json | "$PYTHON" -c 'import json,sys; print("\n".join(json.load(sys.stdin).get("existing_builds") or []))')
  fi
  if supports "$BINARY"; then
    echo "arch '$ARCH': supported by this build"
  else
    echo "arch '$ARCH': no build on this machine lists it; this model may need its own llama.cpp fork" >&2
  fi
fi

STEM="$(basename "${MODEL%.gguf}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//' | cut -c1-40)"
ALIAS="${ALIAS:-$STEM}"
PORT="${PORT:-8130}"
UNIT="${UNIT:-llama-cpp-$STEM}"
GGUF_DIR="$(dirname "$MODEL")"

# A Vulkan build on AMD needs host-visible VMEM disabled or the driver hands out system RAM as if
# it were VRAM and the first long prompt falls off a cliff.
ENVIRONMENT=""
if echo "$BINARY" | grep -qi vulkan; then
  ENVIRONMENT=$'Environment=GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM=1\n'
fi

UNIT_TEXT="[Unit]
Description=llama-server: $ALIAS on 127.0.0.1:$PORT
After=network.target

[Service]
Type=simple
${ENVIRONMENT}ExecStart=$BINARY -m $MODEL --alias $ALIAS --host 127.0.0.1 --port $PORT $FLAGS
Restart=on-failure
RestartSec=3
MemoryMax=90%

[Install]
WantedBy=default.target"

TARGET="$HOME/.config/systemd/user/$UNIT.service"

echo "$UNIT_TEXT"
echo
if [ "$WRITE" = "1" ]; then
  mkdir -p "$(dirname "$TARGET")"
  printf '%s\n' "$UNIT_TEXT" > "$TARGET"
  systemctl --user daemon-reload
  echo "wrote $TARGET (disabled)"
  echo "start with: systemctl --user start $UNIT.service"
else
  echo "not written; re-run with --write to install it, or paste the block above."
fi

if [ "$START" = "1" ]; then
  systemctl --user start "$UNIT.service"
  for _ in $(seq 1 60); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 2 "http://127.0.0.1:$PORT/health" || true)"
    [ "$code" = "200" ] && { echo "ready on :$PORT"; break; }
    sleep 3
  done
  curl -s -o /dev/null -w 'health %{http_code}\n' -m 2 "http://127.0.0.1:$PORT/health" || true
  curl -s "http://127.0.0.1:$PORT/props" 2>/dev/null | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print("model:", d.get("model_path")); print("ctx:", d.get("default_generation_settings",{}).get("n_ctx"))' 2>/dev/null || true
  echo "note: the unit is disabled at boot; start it deliberately or enable it yourself."
fi

echo
echo "model dir: $GGUF_DIR   (weights for a run belong on a local disk, not a USB drive)"
