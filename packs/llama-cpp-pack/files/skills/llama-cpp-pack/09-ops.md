# Ops: units, ports, launchers, monitoring

## A service unit that survives contact with reality

`~/.config/systemd/user/local-llm-example.service`

```ini
[Unit]
Description=llama-server: Example-35B-A3B Q4 (port 8115)
After=network.target

[Service]
Type=simple
Environment=GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM=1
ExecStart=%h/llama.cpp-moe-cache/build-vulkan/bin/llama-server \
  -m %h/models-example/Example-35B-A3B-Q4_0.gguf \
  --alias example-35b \
  --host 127.0.0.1 --port 8115 \
  -ngl 999 -ncmoe 26 -fa on \
  -c 8192 -t 6 -tb 6 \
  -ctk q8_0 -ctv q8_0 \
  --load-mode none --parallel 1 --timeout 1800
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

Notes that cost time to learn:

- `Environment=GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM=1` is a llama.cpp variable name with a typo in it.
  Spelling it "VIDMEM" as the variable does is required.
- `--timeout 1800`. The default two minutes kills long generations on this hardware.
- `--parallel 1` / `-np 1`. Default 4 quarters your context.
- Units stay **disabled**. Nothing LLM-related starts at boot; a model loads because a person asked.

## Port map on this box

| Port | Serves | Notes |
|---|---|---|
| 8100 | Heem Discord bot brain, Qwen3.5-9B uncensored | not managed by the switcher |
| 8101 | Qwen3.8-27B Q3-DOWN-XS, production default | 24.5 t/s, 128k ceiling |
| 8105 | Flint-40B | stopped, unit kept |
| 8106 | Groot-19B-A3B, Hades production | 32k q8_0 KV, 7.34 GB |
| 8110 | jacquard-omni LFM3-Omni-2B (face, ASR, TTS) | |
| 8115 | Clover-35B-A3B | bench |
| 8118 | agon bench server | only during a run |
| 8123 | Hades vision (llama-server with `--mmproj`) | started on demand by hades |
| 8190 | sd-server (image) | |
| 8191 | image gateway + studio | |

`local-llm <name>` from `~/.config/local-llm/models.json` switches models and stops everything else
first. When a model must be started manually, stop the others first or you will measure CPU speed and
call it a model.

## Monitoring one-liners

```bash
# Is it loaded and which model did it actually take?
curl -s localhost:8115/v1/models | python3 -m json.tool | head -20
curl -s localhost:8115/props | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("model_path"), d.get("default_generation_settings",{}).get("n_ctx"))'

# What is the live request doing right now?
curl -s localhost:8115/slots | python3 -c 'import json,sys; s=json.load(sys.stdin)[0]; print({k:s[k] for k in ("is_processing","n_prompt_tokens","n_prompt_tokens_processed")})'

# Memory heaps
cat /sys/class/drm/card1/device/mem_info_vram_used /sys/class/drm/card1/device/mem_info_gtt_used

# The authoritative timing line for the last request
grep 'print_timing' /tmp/example-server.log | tail -2

# Who holds the card
for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -q /dev/dri && echo "$p $(tr '\0' ' ' < $p/cmdline | cut -c1-90)"; done
```

## Health poll after a restart

```bash
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 2 http://127.0.0.1:8115/health)
  [ "$code" = 200 ] && { echo ready; break; }
  sleep 3
done
```

An 18 GB model on SATA root takes 60 to 75 seconds to load. A 12 GB model takes 30 to 45. Poll, do not
sleep a fixed amount.

## Disk and where weights live

- `/home/sc/models` is a symlink to the USB library. Fine for cold storage, too slow to serve from.
- Weights you are about to run or measure belong on the internal SATA SSD under `/` (~500 MB/s).
- Bulk and abandoned weights go to `/mnt/ssd/relocated/<name>` with a symlink left behind, so paths in
  unit files keep working.
- Check `df -h /` before every download. An 18 to 20 GB model is a real fraction of a 218 GB root.
- Raw artifacts, logs and scrapes never go in the knowledge vault; they live under
  `~/.hermes/vault-archive/`.

## Launcher script pattern

```bash
#!/usr/bin/env bash
# Stop anything holding the card, start one model, wait for health, then exec the client.
set -euo pipefail
systemctl --user stop local-llm-other.service jacquard-image.service 2>/dev/null || true
systemctl --user start local-llm-example.service
for i in $(seq 1 40); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' -m 2 http://127.0.0.1:8115/health)" = 200 ] && break
  sleep 3
done
exec "${CLIENT_BIN:-$HOME/.local/bin/client}" --endpoint http://127.0.0.1:8115/v1 "$@"
```

Resolve binaries explicitly. A launcher that depends on the caller's `PATH` breaks the day the tool is
installed somewhere else, and the error reads like the model failed.

## Restore discipline

When a measurement is over, put the box back the way the household uses it: stop the bench server,
start the production model, verify VRAM, and leave every unit `disabled`. A forgotten 20 GB model is a
silent tax on the next job.
