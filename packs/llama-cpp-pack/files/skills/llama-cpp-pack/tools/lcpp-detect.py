#!/usr/bin/env python3
"""Probe a Linux box for what llama.cpp can use, and print a report.

    ./lcpp-detect.py            human summary
    ./lcpp-detect.py --json     machine readable, for lcpp-recommend.py

Standard library only, no root, no network. Every field is read from sysfs, procfs,
lspci, nvidia-smi or a version banner; nothing is guessed from the distro name.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

GIB = 1024 ** 3


def run(command):
    try:
        out = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=20
        )
        return out.stdout.strip()
    except Exception:
        return ""


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return default


def cpu_info():
    flags, model = [], ""
    text = read("/proc/cpuinfo")
    for line in text.splitlines():
        if line.startswith("model name") and not model:
            model = line.split(":", 1)[1].strip()
        if line.startswith("flags") and not flags:
            flags = line.split(":", 1)[1].split()
    # Physical cores: count unique (package, core) pairs.
    cores = set()
    for cpu in Path("/sys/devices/system/cpu").glob("cpu[0-9]*"):
        topology = cpu / "topology"
        try:
            cores.add((read(topology / "physical_package_id"), read(topology / "core_id")))
        except Exception:
            continue
    physical = len(cores) or (os.cpu_count() or 1)
    wanted = {
        "avx2": "avx2",
        "avx512f": "avx512f",
        "avx512_vnni": "avx512_vnni",
        "avx512_bf16": "avx512_bf16",
        "amx_int8": "amx_int8",
        "f16c": "f16c",
        "fma": "fma",
    }
    return {
        "model": model,
        "logical": os.cpu_count() or 1,
        "physical": physical,
        "isa": sorted({name for name, flag in wanted.items() if flag in flags}),
        "has_avx512": "avx512f" in flags,
    }


def mem_gib():
    text = read("/proc/meminfo")
    values = {}
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) == 2:
            values[parts[0]] = parts[1].strip()
    def gib(key):
        raw = values.get(key, "0 kB").split()[0]
        return round(int(raw) / 1024 / 1024, 1)
    return {"ram_gib": gib("MemTotal"), "swap_gib": gib("SwapTotal")}


def gpu_from_sysfs():
    gpus = []
    seen = set()
    vendors = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        # Only the card itself; card1-DP-1 and card1-HDMI-A-1 are connectors whose
        # "device" is the DRM node, not the PCI function.
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device = card / "device"
        if not device.exists():
            continue
        # card0, card1 and connectors (card1-DP-1) all resolve to the same PCI device.
        key = os.path.realpath(device)
        if key in seen or "/drm/" in key:
            continue
        seen.add(key)
        vendor = read(device / "vendor")
        pci = device.resolve().name
        name = ""
        for line in run(f"lspci -s {pci}").splitlines():
            if "] " in line:
                name = line.split("] ", 1)[1].strip()
                name = name.split(": ", 1)[1].strip() if ": " in name else name
            elif "controller: " in line:
                name = line.split("controller: ", 1)[1].strip()
        if "Advanced Micro Devices, Inc. [AMD/ATI] " in name:
            name = name.split("[AMD/ATI] ", 1)[1]
        entry = {
            "device": str(device),
            "vendor": vendors.get(vendor, vendor or "unknown"),
            "name": name or "unknown GPU",
            "pci": pci,
            "vram_gib": None,
            "driver": os.path.basename(os.path.realpath(device / "driver"))
            if (device / "driver").exists()
            else "",
        }
        total = read(device / "mem_info_vram_total")
        if total.isdigit():
            entry["vram_gib"] = round(int(total) / GIB, 2)
        gtt = read(device / "mem_info_gtt_total")
        if gtt.isdigit():
            entry["gtt_gib"] = round(int(gtt) / GIB, 2)
        gpus.append(entry)
    return gpus


def gpu_from_nvidia():
    if not shutil.which("nvidia-smi"):
        return []
    query = (
        "nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap "
        "--format=csv,noheader,nounits"
    )
    gpus = []
    for line in run(query).splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            entry = {
                "vendor": "NVIDIA",
                "name": parts[0],
                "vram_gib": round(float(parts[1]) / 1024, 2),
                "driver": parts[2],
            }
            if len(parts) >= 4:
                entry["compute_cap"] = parts[3]
            gpus.append(entry)
    return gpus


def backends(gpus):
    found = {}
    icds = list(Path("/usr/share/vulkan/icd.d").glob("*.json")) if Path(
        "/usr/share/vulkan/icd.d").exists() else []
    found["vulkan"] = {
        "available": bool(icds),
        "icd": [p.name for p in icds],
        "vulkaninfo": bool(shutil.which("vulkaninfo")),
    }
    rocm = Path("/opt/rocm")
    found["rocm"] = {
        "available": rocm.exists() or bool(shutil.which("rocminfo")),
        "path": str(rocm) if rocm.exists() else "",
        "hipcc": bool(shutil.which("hipcc")),
    }
    cuda_paths = [p for p in ("/usr/local/cuda", "/opt/cuda") if Path(p).exists()]
    found["cuda"] = {
        "available": bool(shutil.which("nvcc")) or bool(cuda_paths),
        "nvcc": run("nvcc --version 2>/dev/null | tail -1") if shutil.which("nvcc") else "",
    }
    found["cpu_only_fallback"] = True
    if not gpus:
        found["note"] = "no GPU found; CPU build is the only option"
    return found


def disks():
    rows = []
    for mount in ["/", str(Path.home())] + [str(p) for p in Path("/mnt").glob("*")]:
        if not os.path.ismount(mount):
            continue
        try:
            usage = shutil.disk_usage(mount)
        except Exception:
            continue
        rotational = ""
        source = run(f"findmnt -no SOURCE {mount}")
        if source.startswith("/dev/"):
            base = os.path.basename(source).rstrip("0123456789")
            for candidate in Path("/sys/block").glob("*"):
                if base.startswith(candidate.name) or candidate.name.startswith(base):
                    rotational = read(candidate / "queue" / "rotational")
                    break
        rows.append({
            "mount": mount,
            "free_gib": round(usage.free / GIB, 1),
            "total_gib": round(usage.total / GIB, 1),
            "device": source,
            "rotational": {"1": "hdd", "0": "ssd"}.get(rotational, "unknown"),
        })
    return rows


def toolchain():
    def version(command):
        out = run(f"{command} 2>/dev/null | head -1")
        return out.strip()
    return {
        "cmake": version("cmake --version"),
        "compiler": version("cc --version"),
        "ninja": version("ninja --version"),
        "git": version("git --version"),
        "python": platform.python_version(),
    }


def existing_builds():
    builds = []
    for pattern in ("~/llama.cpp*/build*/bin/llama-server", "/opt/llama.cpp*/build*/bin/llama-server",
                    "~/llama-*/build*/bin/llama-server"):
        for path in Path.home().glob(pattern.replace("~/", "")) if pattern.startswith("~/") else Path("/").glob(pattern.lstrip("/")):
            builds.append(str(path))
    return sorted(set(builds))


def main():
    gpus = gpu_from_sysfs()
    nvidia = gpu_from_nvidia()
    if nvidia:
        gpus = [g for g in gpus if g["vendor"] != "NVIDIA"] + nvidia
    report = {
        "host": platform.node(),
        "os": {
            "distro": read("/etc/os-release").split("\n")[0].split("=")[-1].strip('"') if Path("/etc/os-release").exists() else "",
            "kernel": platform.release(),
            "arch": platform.machine(),
        },
        "cpu": cpu_info(),
        "memory": mem_gib(),
        "gpus": gpus,
        "backends": backends(gpus),
        "disks": disks(),
        "toolchain": toolchain(),
        "existing_builds": existing_builds(),
    }
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
        return

    cpu = report["cpu"]
    mem = report["memory"]
    print(f"{report['os']['distro']} {report['os']['kernel']} {report['os']['arch']} on {report['host']}")
    print(f"CPU  {cpu['model']}  {cpu['physical']}c/{cpu['logical']}t  isa={','.join(cpu['isa']) or 'baseline'}")
    print(f"RAM  {mem['ram_gib']} GiB  swap {mem['swap_gib']} GiB")
    for gpu in report["gpus"]:
        vram = f"{gpu.get('vram_gib')} GiB" if gpu.get("vram_gib") else "unknown VRAM"
        print(f"GPU  {gpu['vendor']} {gpu['name']}  {vram}  driver={gpu.get('driver','?')}  pci={gpu.get('pci','?')}")
    if not report["gpus"]:
        print("GPU  none detected")
    for name, info in report["backends"].items():
        if isinstance(info, dict) and "available" in info:
            print(f"BACKEND {name:<6} {'yes' if info['available'] else 'no'}")
    for disk in report["disks"]:
        print(f"DISK {disk['mount']:<8} {disk['free_gib']:>7} GiB free of {disk['total_gib']:>7}  {disk['rotational']}  {disk['device']}")
    tools = report["toolchain"]
    print(f"TOOLS cmake={'yes' if tools['cmake'] else 'no'} compiler={'yes' if tools['compiler'] else 'no'} ninja={'yes' if tools['ninja'] else 'no'}")
    for build in report["existing_builds"]:
        print(f"BUILD {build}")


if __name__ == "__main__":
    main()
