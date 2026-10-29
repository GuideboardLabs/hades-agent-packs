#!/usr/bin/env python3
"""Adapt any agent pack to the machine it was just installed on.

A pack is written on one computer, by one person, for one set of circumstances. Installed anywhere
else it arrives carrying assumptions: how much VRAM exists, which port is free, which backend the
GPU wants, how many physical cores there are, where the weights live, which config file the agent
reads. Copying the files in is not installing the pack; it is inheriting someone else's machine.

This script is the middle man. It reads the pack's `pack.manifest.json`, checks every assumption
the pack declares against the live system, reports which are false with a concrete remedy, and
writes a profile.json that the pack's own tools read. It never silently adjusts anything and never
edits the pack's documents: it tells the agent what to change and why.

    ./pack-adapt.py                          check, report, write the profile
    ./pack-adapt.py --dry-run                report only
    ./pack-adapt.py --pack /path/to/pack     adapt a pack somewhere else
    ./pack-adapt.py --json report.json       keep the full report
    ./pack-adapt.py --show                   print the profile written last time

Exit status: 0 all hard assumptions hold, 3 a hard assumption is false, 4 something could not be
checked. A pack is not installed until this returns 0 or 4 with the conflicts understood.

Manifest shape (only `name` and `assumptions` are required):

    {
      "name": "my-pack",
      "version": "1.0.0",
      "checker": "tools/my-checks.py",         // optional domain checks
      "settings": {"threads": "${physical_cores}", "port": "${free_port}"},
      "assumptions": [
        {"id": "vram", "claim": "8 GiB of VRAM", "check": "vram_at_least", "arg": 8,
         "severity": "soft", "resolution": "recompute the offload for this card"}
      ]
    }

A checker module exposes `CHECKS = {name: fn(arg, ctx) -> {"status", "detail", "resolution"}}`
where status is "ok", "conflict" or "unknown".
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import socket
import subprocess
import sys
from pathlib import Path

GIB = 1024 ** 3


# ---------------------------------------------------------------- host facts


def read(path) -> str:
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return ""


def run(cmd: str, timeout: int = 30) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def probe(info_path: str | None) -> dict:
    """Hardware facts. Uses the pack's own detector when it ships one, else built-ins."""
    if info_path:
        proc = subprocess.run([sys.executable, info_path, "--json"], capture_output=True, text=True)
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                pass
    mem = read("/proc/meminfo")
    total_kb = 0
    for line in mem.splitlines():
        if line.startswith("MemTotal:"):
            total_kb = int(line.split()[1])
    flags = ""
    physical = None
    pairs = set()
    current_phys = None
    for line in read("/proc/cpuinfo").splitlines():
        if line.startswith("processor"):
            current_phys = None
        elif line.startswith("core id"):
            current_phys = line.split(":")[1].strip()
        elif line.startswith("physical id") and current_phys is not None:
            pairs.add((line.split(":")[1].strip(), current_phys))
        elif line.startswith("flags") and not flags:
            flags = line.split(":", 1)[1]
    if pairs:
        physical = len(pairs)
    return {
        "cpu": {"physical": physical, "logical": os.cpu_count(), "isa": flags.split()[:40]},
        "memory": {"ram_gib": round(total_kb / 1024 / 1024, 1) if total_kb else None},
    }


def primary_gpu(info: dict) -> dict:
    gpus = info.get("gpus") or []
    if not gpus:
        return {}
    return max(gpus, key=lambda g: float(g.get("vram_gib") or 0))


def gpu_vram_in_use(gpu: dict) -> float | None:
    device = gpu.get("device") or ""
    if device:
        used = read(Path(device) / "mem_info_vram_used")
        if used.isdigit():
            return round(int(used) / GIB, 2)
    if gpu.get("vendor") == "NVIDIA" and gpu.get("index") is not None:
        out = run(f"nvidia-smi --id={gpu['index']} --query-gpu=memory.used --format=csv,noheader,nounits")
        first = out.splitlines()[0].strip() if out.splitlines() else ""
        if first.isdigit():
            return round(int(first) / 1024, 2)
    return None


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.settimeout(0.4)
        return probe_socket.connect_ex(("127.0.0.1", port)) != 0


def free_port(start: int = 8100, span: int = 120) -> int:
    for candidate in range(start, start + span):
        if port_free(candidate):
            return candidate
    return start


def path_is_local(path: Path) -> bool:
    target = path.resolve()
    device = None
    for line in read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = parts[1].replace("\\040", " ")
        if str(target).startswith(mount) and (device is None or len(mount) >= len(device[1])):
            device = (parts[0], mount)
    if not device or not device[0].startswith("/dev/"):
        return True
    match = re.match(r"(nvme\d+n\d+|mmcblk\d+|sd[a-z]+|vd[a-z]+)", Path(device[0]).name)
    if not match:
        return True
    return read(f"/sys/block/{match.group(1)}/queue/rotational") != "1"


# ---------------------------------------------------------------- builtin checks


def builtin_checks() -> dict:
    def gpu_present(arg, ctx):
        gpu = ctx["gpu"]
        if gpu:
            return {"status": "ok", "detail": f"{gpu.get('name')} ({gpu.get('vram_gib')} GiB)"}
        return {"status": "conflict", "detail": "no GPU found",
                "resolution": "CPU only: the pack's GPU advice does not apply, expect single-digit t/s above 4B"}

    def gpu_vendor(arg, ctx):
        vendor = (ctx["gpu"].get("vendor") or "none")
        if vendor.lower() == str(arg).lower():
            return {"status": "ok", "detail": vendor}
        return {"status": "conflict", "detail": f"vendor is {vendor}, pack assumed {arg}",
                "resolution": f"translate the pack's backend advice to {vendor} before following it"}

    def vram_at_least(arg, ctx):
        vram = float(ctx["gpu"].get("vram_gib") or 0)
        if not vram:
            return {"status": "unknown", "detail": "VRAM unreadable"}
        need = float(arg or 0)
        if vram >= need * 0.95:
            return {"status": "ok", "detail": f"{vram:g} GiB"}
        return {"status": "conflict", "detail": f"{vram:g} GiB, pack assumed {need:g}",
                "resolution": "recompute what fits; do not copy the pack's offload numbers"}

    def vram_at_most(arg, ctx):
        vram = float(ctx["gpu"].get("vram_gib") or 0)
        if not vram:
            return {"status": "unknown", "detail": "VRAM unreadable"}
        if vram <= float(arg):
            return {"status": "ok", "detail": f"{vram:g} GiB"}
        return {"status": "conflict", "detail": f"{vram:g} GiB, pack assumed at most {arg}",
                "resolution": "more memory than assumed: raise context or lower the offload, then re-measure"}

    def physical_cores(arg, ctx):
        cores = ctx["info"].get("cpu", {}).get("physical")
        if not cores:
            return {"status": "unknown", "detail": "physical core count unreadable"}
        return {"status": "ok", "detail": f"{cores} physical, {ctx['info'].get('cpu', {}).get('logical')} logical",
                "resolution": f"use {cores} threads"}

    def port_free_check(arg, ctx):
        port = int(arg or 0)
        if port_free(port):
            return {"status": "ok", "detail": f"{port} free"}
        return {"status": "conflict", "detail": f"{port} in use",
                "resolution": f"use {free_port()} instead"}

    def vram_idle(arg, ctx):
        used = gpu_vram_in_use(ctx["gpu"]) if ctx["gpu"] else None
        if used is None:
            return {"status": "unknown", "detail": "VRAM in use unreadable"}
        if used <= float(arg or 0.9):
            return {"status": "ok", "detail": f"{used:g} GiB in use"}
        return {"status": "conflict", "detail": f"{used:g} GiB already loaded",
                "resolution": "stop the other process: any measurement now describes the contention, not the pack"}

    def ram_at_least(arg, ctx):
        ram = float(ctx["info"].get("memory", {}).get("ram_gib") or 0)
        if not ram:
            return {"status": "unknown", "detail": "RAM unreadable"}
        if ram >= float(arg or 0):
            return {"status": "ok", "detail": f"{ram:g} GiB"}
        return {"status": "conflict", "detail": f"{ram:g} GiB, pack assumed {arg:g}",
                "resolution": "choose smaller weights: offload into swap is slower than not running"}

    def disk_local(arg, ctx):
        raw = arg if isinstance(arg, list) else [arg]
        targets = [Path(os.path.expanduser(str(p))) for p in raw if p]
        if not targets:
            return {"status": "unknown", "detail": "no path given to check"}
        slow = [str(p) for p in targets if p.exists() and not path_is_local(p)]
        missing = [str(p) for p in targets if not p.exists()]
        if slow:
            return {"status": "conflict", "detail": f"rotational or USB: {', '.join(slow)}",
                    "resolution": "move the working data to an internal disk"}
        if missing:
            return {"status": "unknown", "detail": f"does not exist yet: {', '.join(missing)}"}
        return {"status": "ok", "detail": ", ".join(str(p) for p in targets)}

    def file_exists(arg, ctx):
        path = Path(os.path.expanduser(str(arg)))
        if path.exists():
            return {"status": "ok", "detail": str(path)}
        return {"status": "conflict", "detail": f"missing: {path}",
                "resolution": "install that prerequisite before using the pack"}

    def binary_flag(arg, ctx):
        binary, flag = arg if isinstance(arg, list) else (arg, "")
        path = shutil_which(binary)
        if not path:
            return {"status": "conflict", "detail": f"{binary} not on PATH",
                    "resolution": "build or install it"}
        text = run(f"{shlex.quote(path)} --version 2>&1; {shlex.quote(path)} --help 2>&1")
        if flag and flag not in text:
            return {"status": "conflict", "detail": f"{path} lacks {flag}",
                    "resolution": f"this build cannot do what the pack needs; rebuild with it enabled"}
        return {"status": "ok", "detail": f"{path} has {flag or 'a version banner'}"}

    def socket_alive(arg, ctx):
        url = str(arg)
        host_port = re.sub(r"^[a-z]+://", "", url).split("/")[0]
        if ":" not in host_port:
            return {"status": "unknown", "detail": f"no port in {url}"}
        host, port = host_port.rsplit(":", 1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            probe_socket.settimeout(0.5)
            alive = probe_socket.connect_ex((host, int(port))) == 0
        if alive:
            return {"status": "ok", "detail": f"{url} answers"}
        return {"status": "conflict", "detail": f"{url} is not listening",
                "resolution": "start it, or point the config at something that exists"}

    def config_json(arg, ctx):
        path = Path(os.path.expanduser(str(arg)))
        if not path.exists():
            return {"status": "unknown", "detail": f"{path} does not exist"}
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as error:
            return {"status": "conflict", "detail": f"{path}: {error}",
                    "resolution": "fix the syntax before any tool reads it"}
        return {"status": "ok", "detail": f"{path} is valid JSON"}

    def service_active(arg, ctx):
        unit = str(arg)
        state = run(f"systemctl --user is-active {shlex.quote(unit)}")
        if state == "active":
            return {"status": "ok", "detail": f"{unit} active"}
        return {"status": "conflict", "detail": f"{unit} is {state or 'unknown'}",
                "resolution": f"systemctl --user start {unit}"}

    return {
        "gpu_present": gpu_present,
        "gpu_vendor": gpu_vendor,
        "vram_at_least": vram_at_least,
        "vram_at_most": vram_at_most,
        "physical_cores": physical_cores,
        "port_free": port_free_check,
        "vram_idle": vram_idle,
        "ram_at_least": ram_at_least,
        "disk_local": disk_local,
        "file_exists": file_exists,
        "binary_flag": binary_flag,
        "socket_alive": socket_alive,
        "config_json": config_json,
        "service_active": service_active,
    }


def shutil_which(name: str) -> str:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


# ---------------------------------------------------------------- engine


def load_checker(path: Path) -> dict:
    if not path.exists():
        print(f"warning: checker {path} not found", file=sys.stderr)
        return {}
    spec = importlib.util.spec_from_file_location("pack_checks", path)
    if spec is None or spec.loader is None:
        print(f"warning: could not load checker {path}", file=sys.stderr)
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "CHECKS", {})


def fill(template, ctx):
    """Resolve ${name} in a settings template from the facts."""
    flat = {
        "physical_cores": ctx["info"].get("cpu", {}).get("physical") or os.cpu_count() or 1,
        "logical_cores": os.cpu_count() or 1,
        "ram_gib": ctx["info"].get("memory", {}).get("ram_gib"),
        "vram_gib": ctx["gpu"].get("vram_gib"),
        "vendor": ctx["gpu"].get("vendor"),
        "free_port": free_port(),
        "home": str(Path.home()),
        "pack": str(ctx["pack"]),
    }
    if isinstance(template, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: str(flat.get(m.group(1), "")), template)
    if isinstance(template, dict):
        return {k: fill(v, ctx) for k, v in template.items()}
    if isinstance(template, list):
        return [fill(v, ctx) for v in template]
    return template


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a pack's assumptions against this machine.")
    parser.add_argument("--pack", help="pack directory (default: the one this script lives in)")
    parser.add_argument("--manifest", help="explicit manifest path")
    parser.add_argument("--dry-run", action="store_true", help="report only")
    parser.add_argument("--json", help="write the full report here")
    parser.add_argument("--profile", help="profile output path")
    parser.add_argument("--show", action="store_true", help="print the profile from last time")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    pack = Path(args.pack).resolve() if args.pack else here.parent
    manifest_path = Path(args.manifest) if args.manifest else pack / "pack.manifest.json"
    profile_path = Path(os.path.expanduser(args.profile)) if args.profile else Path.home() / ".config" / "pack-adapt" / f"{pack.name}.profile.json"

    if args.show:
        print(profile_path.read_text() if profile_path.exists() else f"no profile at {profile_path}")
        return 0
    if not manifest_path.exists():
        print(f"no manifest at {manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text())

    detector = pack / "tools" / "lcpp-detect.py"
    info = probe(str(detector) if detector.exists() else None)
    gpu = primary_gpu(info)
    ctx = {"info": info, "gpu": gpu, "pack": pack, "manifest": manifest}

    checks = builtin_checks()
    if manifest.get("checker"):
        checks.update(load_checker(pack / manifest["checker"]))

    rows = []
    for assumption in manifest.get("assumptions", []):
        name = assumption.get("check", "")
        handler = checks.get(name)
        if handler is None:
            outcome = {"status": "unknown", "detail": f"no check named {name}", "resolution": "the manifest is newer than this engine"}
        else:
            try:
                outcome = handler(assumption.get("arg"), ctx)
            except Exception as error:  # a broken check must not read as a pass
                outcome = {"status": "unknown", "detail": f"check {name} raised {error!r}", "resolution": "fix the checker"}
        rows.append({"id": assumption.get("id", name), "claim": assumption.get("claim", ""),
                     "severity": assumption.get("severity", "soft"), **outcome})

    mark = {"ok": "ok   ", "conflict": "FALSE", "unknown": "?    "}
    print(f"pack:       {manifest.get('name')} {manifest.get('version', '')}")
    written = manifest.get("written_on") or {}
    if written:
        print("written on: " + ", ".join(f"{k}={v}" for k, v in written.items()))
    print(f"running on: {gpu.get('name') or 'no GPU'} / {gpu.get('vram_gib')} GiB VRAM / "
          f"{ctx['info'].get('cpu', {}).get('physical')} cores / {ctx['info'].get('memory', {}).get('ram_gib')} GiB RAM")
    print()
    for row in rows:
        print(f"[{mark.get(row['status'], '?')}] {row['id']}: {row['claim']}")
        if row.get("detail"):
            print(f"          now: {row['detail']}")
        if row["status"] != "ok" and row.get("resolution"):
            print(f"          do:  {row['resolution']}")

    conflicts = [r for r in rows if r["status"] == "conflict"]
    unknowns = [r for r in rows if r["status"] == "unknown"]
    hard = [r for r in conflicts if r["severity"] == "hard"]
    print()
    print(f"{len(rows) - len(conflicts) - len(unknowns)} of {len(rows)} assumptions hold, "
          f"{len(conflicts)} false, {len(unknowns)} unverifiable")

    profile = {
        "pack": manifest.get("name"),
        "pack_version": manifest.get("version"),
        "adapted_for": {
            "gpu": gpu.get("name"),
            "vendor": gpu.get("vendor"),
            "vram_gib": gpu.get("vram_gib"),
            "physical_cores": ctx["info"].get("cpu", {}).get("physical"),
            "ram_gib": ctx["info"].get("memory", {}).get("ram_gib"),
        },
        "settings": fill(manifest.get("settings", {}), ctx),
        "conflicts": [{k: r[k] for k in ("id", "claim", "detail", "resolution")} for r in conflicts],
        "unverified": [r["id"] for r in unknowns],
        "remeasure": manifest.get("claims_to_remeasure", []),
    }
    if not args.dry_run:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(profile, indent=2) + "\n")
        print(f"profile written: {profile_path}")
    if args.json:
        Path(args.json).write_text(json.dumps({"manifest": manifest, "checks": rows, "profile": profile}, indent=2) + "\n")
        print(f"report written: {args.json}")
    if profile["remeasure"]:
        print()
        print("Numbers measured on other hardware do not transfer. Re-measure before quoting:")
        for claim in profile["remeasure"]:
            print(f"  {claim.get('metric')} = {claim.get('value')}  ({claim.get('hardware')})")
    if hard:
        print()
        print("STOP: " + ", ".join(r["id"] for r in hard) + " must be fixed before the pack's results mean anything.")
        return 3
    return 4 if unknowns else 0


if __name__ == "__main__":
    sys.exit(main())
