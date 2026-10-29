#!/usr/bin/env python3
"""Checks specific to the llama.cpp serving pack.

`pack-adapt.py` supplies the machine-shaped checks (VRAM, cores, ports, disks, sockets). These are
the ones that need to know what llama.cpp is: whether ROCm supports this silicon, whether the built
binary has the flags the pack relies on, and whether the Hades agent's own config points at a server
that exists.

    CHECKS = {name: fn(arg, ctx) -> {"status": "ok" | "conflict" | "unknown", "detail", "resolution"}}
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
from pathlib import Path

SUPPORTED_ROCM = {"gfx900", "gfx906", "gfx908", "gfx90a", "gfx942", "gfx1030",
                  "gfx1100", "gfx1101", "gfx1102", "gfx1200", "gfx1201"}


def run(cmd: str, timeout: int = 20) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def rocm_target() -> str:
    if not run("command -v rocminfo"):
        return ""
    return run("rocminfo 2>/dev/null | grep -m1 -o 'gfx[0-9a-f]*'") or ""


def check_rocm_support(arg, ctx):
    target = rocm_target()
    if not target:
        return {"status": "unknown",
                "detail": "rocminfo not installed, ROCm support for this GPU is unknown",
                "resolution": "if this is gfx1030 or newer, install ROCm and compare builds before accepting Vulkan"}
    if target in SUPPORTED_ROCM:
        return {"status": "conflict",
                "detail": f"{target} is supported by ROCm, but this pack chose Vulkan for gfx1010",
                "resolution": f"build with -DGGML_HIP=ON -DAMDGPU_TARGETS={target} and measure both; "
                              "field reports show 30 to 50 percent more decode where ROCm is supported"}
    return {"status": "ok", "detail": f"{target} is outside ROCm's supported list, Vulkan is the correct choice"}


def check_binary_features(arg, ctx):
    builds = ctx["info"].get("existing_builds") or []
    binary = builds[0] if builds else ""
    if not binary:
        return {"status": "conflict", "detail": "no llama-server found",
                "resolution": "run tools/lcpp-build.sh, then re-run this"}
    help_text = run(f"'{binary}' --help 2>&1")
    if not help_text:
        return {"status": "conflict", "detail": f"{binary} does not run",
                "resolution": "rebuild; a server that cannot print help cannot serve"}
    missing = [flag for flag in ("--mmproj", "n-cpu-moe") if flag not in help_text]
    if missing:
        return {"status": "conflict", "detail": f"{binary} lacks {' and '.join(missing)}",
                "resolution": "build from current master: without n-cpu-moe a 35B MoE cannot fit a small card, "
                              "and without --mmproj vision is a text-only illusion"}
    return {"status": "ok", "detail": f"{binary} has --mmproj and n-cpu-moe"}


def hades_config() -> dict:
    path = Path.home() / ".hades" / "config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        return {"error": str(error), "path": str(path)}
    model = data.get("model") or data.get("model_name") or ""
    endpoints = data.get("endpoints") or {}
    chosen = endpoints.get(model) or {}
    return {
        "path": str(path),
        "model": model,
        "endpoint": chosen.get("url") or chosen.get("endpoint") or "",
        "context_tokens": chosen.get("context_tokens"),
        "thinking": chosen.get("thinking"),
        "vision_configured": bool(data.get("vision")),
    }


def check_hades_endpoint(arg, ctx):
    cfg = hades_config()
    if not cfg:
        return {"status": "unknown", "detail": "no ~/.hades/config.json: this pack was installed outside Hades",
                "resolution": "if Hades is used, point its endpoint at the tuned server and match context_tokens to n_ctx"}
    if cfg.get("error"):
        return {"status": "conflict", "detail": f"{cfg['path']}: {cfg['error']}",
                "resolution": "fix the JSON before any tool reads it"}
    url = cfg.get("endpoint") or ""
    if not url:
        return {"status": "conflict", "detail": f"model {cfg.get('model')} has no endpoint",
                "resolution": "add an endpoints entry pointing at the tuned server"}
    host_port = re.sub(r"^[a-z]+://", "", url).split("/")[0]
    if ":" not in host_port:
        return {"status": "unknown", "detail": f"no port in {url}"}
    host, port = host_port.rsplit(":", 1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        alive = probe.connect_ex((host, int(port))) == 0
    detail = (f"model {cfg.get('model')} -> {url} ({'alive' if alive else 'dead'}), "
              f"ctx {cfg.get('context_tokens')}, thinking {cfg.get('thinking')}, "
              f"vision {'configured' if cfg.get('vision_configured') else 'not configured'}")
    if not alive:
        return {"status": "conflict", "detail": detail,
                "resolution": "the endpoint Hades points at is not listening: start its unit, or repoint the config"}
    return {"status": "ok", "detail": detail}


def check_agent_vision(arg, ctx):
    cfg = hades_config()
    if not cfg or cfg.get("error"):
        return {"status": "unknown", "detail": "no readable Hades config"}
    env_keys = [k for k in ("HADES_VISION_URL", "HADES_VISION_MODEL_NAME", "HADES_VISION_BIN") if __import__("os").environ.get(k)]
    if cfg.get("vision_configured"):
        return {"status": "ok", "detail": "vision block present in config.json"}
    if env_keys:
        return {"status": "ok", "detail": f"vision comes from the environment: {', '.join(env_keys)}"}
    return {"status": "conflict", "detail": "vision is not wired: no config block, no environment",
            "resolution": "start hades-vision.service and export HADES_VISION_BIN/_MODEL/_PROJECTOR/_PORT, "
                          "or add a vision block with url/model/key_env"}


CHECKS = {
    "rocm_unsupported_ok": check_rocm_support,
    "binary_features": check_binary_features,
    "hades_endpoint": check_hades_endpoint,
    "agent_vision": check_agent_vision,
}
