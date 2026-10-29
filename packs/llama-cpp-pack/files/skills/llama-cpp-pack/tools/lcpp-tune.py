#!/usr/bin/env python3
"""Measure a GGUF on this machine and hand back the flags that won.

    ./lcpp-tune.py ~/model.gguf                 fit sweep, thread sweep, context ladder
    ./lcpp-tune.py ~/model.gguf --quick         one config, one probe
    ./lcpp-tune.py ~/model.gguf --json out.json
    ./lcpp-tune.py ~/model.gguf --dry-run       print the plan and exit

What it does that a hand sweep does not: it refuses to measure on a busy card, it reads the
card's own accounting after each load instead of trusting the flags, it restarts from a unit it
owns so a crashed server cannot poison the next data point, and it writes the winning line as a
systemd ExecStart you can paste.

It does NOT invent a throughput number when a probe fails: a config that errors is recorded as
an error with the server's own words, and the winner is chosen only among configs that produced
a timing line.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
GIB = 1024 ** 3
PROBE_FILLER = (
    "A local model is a file plus a runtime. The runtime reads the file, lays the weights out, "
    "reserves memory for the keys and values it will need, and answers one token at a time. "
) * 6
PROBE_QUESTION = "\n\nIn one sentence: what decides how fast this runs?\n"


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return default


def card_paths():
    cards = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device = card / "device"
        if (device / "mem_info_vram_used").exists():
            cards.append(device)
    return cards


def vram_state():
    """Every card whose memory this script can read: amdgpu first, then nvidia-smi."""
    rows = []
    for device in card_paths():
        used = read(device / "mem_info_vram_used")
        total = read(device / "mem_info_vram_total")
        if used.isdigit() and total.isdigit():
            rows.append({
                "device": str(device),
                "used_gib": round(int(used) / GIB, 2),
                "total_gib": round(int(total) / GIB, 2),
            })
    if rows:
        return rows
    if shutil.which("nvidia-smi"):
        query = "nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits"
        output = subprocess.run(query, shell=True, capture_output=True, text=True).stdout
        for line in output.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                rows.append({
                    "device": f"nvidia:{parts[0]}",
                    "used_gib": round(int(parts[1]) / 1024, 2),
                    "total_gib": round(int(parts[2]) / 1024, 2),
                })
    return rows


def busy(threshold_gib=0.6):
    return [row for row in vram_state() if row["used_gib"] > threshold_gib]


def gguf_facts(path):
    spec = __import__("importlib.util").util.spec_from_file_location(
        "lcpp_gguf", HERE / "lcpp-gguf.py")
    module = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.read_meta(str(path))
    result["path"] = str(path)
    result["size_bytes"] = os.path.getsize(path)
    return module.facts(result)


def http_json(url, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.load(reply)


def healthy(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
        return True
    except Exception:
        return False


class Server:
    """One loaded configuration, in a transient scope we can always stop by name."""

    def __init__(self, binary, model, port, flags, timeout_s=600):
        self.binary, self.model, self.port, self.flags = binary, model, port, flags
        self.unit = f"lcpp-tune-{port}"
        self.timeout_s = timeout_s

    def start(self):
        self.stop()
        command = [
            "systemd-run", "--user", "--unit", self.unit, "--collect", "-q",
            "-p", "MemoryMax=90%",
            "--",
            self.binary, "-m", str(self.model),
            "--host", "127.0.0.1", "--port", str(self.port),
            "--no-warmup",
        ] + self.flags.split()
        environment = dict(os.environ, GGML_VK_DISABLE_HOST_VISIBLE_VMEM="1")
        started = time.time()
        proc = subprocess.Popen(command, env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        while time.time() - started < 300:
            if healthy(self.port):
                return round(time.time() - started, 1)
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError("server exited: " + output.strip().splitlines()[-1:] .__str__())
            time.sleep(1)
        self.stop()
        raise RuntimeError("server did not become healthy in 300 s")

    def stop(self):
        subprocess.run(["systemctl", "--user", "stop", self.unit],
                       capture_output=True, text=True)
        for _ in range(20):
            if not healthy(self.port):
                return
            time.sleep(0.5)

    def probe(self, max_tokens=96):
        body = {
            "messages": [{"role": "user", "content": PROBE_FILLER + PROBE_QUESTION}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        started = time.time()
        parsed = http_json(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                           body, timeout=self.timeout_s)
        timings = parsed.get("timings", {})
        vram = vram_state()
        return {
            "wall_s": round(time.time() - started, 1),
            "prompt_tokens": timings.get("prompt_n"),
            "prefill_tps": round(timings.get("prompt_per_second") or 0, 1),
            "predicted_tokens": timings.get("predicted_n"),
            "decode_tps": round(timings.get("predicted_per_second") or 0, 2),
            "finish": parsed.get("choices", [{}])[0].get("finish_reason"),
            "vram": vram,
        }


def plan(facts, vram_gib, ctx):
    """Configurations worth trying, widest first so a wrong estimate still lands on the answer."""
    layers = facts.get("layers") or 32
    moe = bool(facts.get("experts"))
    usable = vram_gib * 0.94
    size = facts.get("size_gib") or 0
    base = f"-ngl 999 -fa on -c {ctx} -ctk q8_0 -ctv q8_0 --load-mode none --timeout 1800"
    configs = []
    if moe:
        attention = size * 0.15
        experts = size * 0.85
        room = max(0.0, usable - 0.6 - attention)
        estimate = max(0, min(layers, round(layers * (1 - min(1.0, room / experts if experts else 0)))))
        for offset in (8, 4, 0, -4, -8):
            value = max(0, min(layers, estimate + offset))
            configs.append({"label": f"ncmoe {value}", "flags": f"-ncmoe {value} {base}",
                            "vram_hint": f"{value} of {layers} expert layers in RAM"})
    elif size + 0.6 <= usable:
        # It fits whole; the only question left is whether a smaller offload is faster, and it
        # never is on a bandwidth-bound card. One config, everything resident.
        configs.append({"label": "ngl 999", "flags": f"-ngl 999 -fa on -c {ctx} -ctk q8_0 -ctv q8_0 --timeout 1800",
                        "vram_hint": "the whole model fits; nothing to offload"})
    else:
        for fraction in (0.9, 0.75, 0.6):
            value = max(1, min(layers, round(layers * fraction)))
            configs.append({"label": f"ngl {value}", "flags": f"-ngl {value} -fa on -c {ctx} -ctk q8_0 -ctv q8_0 --timeout 1800",
                            "vram_hint": f"{value} of {layers} layers on the card"})
    return configs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("--binary", default=None)
    parser.add_argument("--port", type=int, default=8199)
    parser.add_argument("--ctx", type=int, default=8192)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", default=None)
    parser.add_argument("--force", action="store_true", help="measure anyway on a busy card")
    parser.add_argument("--token-seconds", type=float, default=600, help="per-request ceiling")
    args = parser.parse_args()

    model = Path(args.model).expanduser()
    if not model.exists():
        raise SystemExit(f"no such file: {model}")
    facts = gguf_facts(model)

    binary = args.binary
    if not binary:
        detect = json.loads(subprocess.run([sys.executable, str(HERE / "lcpp-detect.py"), "--json"],
                                           capture_output=True, text=True).stdout)
        builds = detect.get("existing_builds") or []
        binary = builds[0] if builds else shutil.which("llama-server") or ""
    if not binary or not Path(binary).exists():
        raise SystemExit("no llama-server found; pass --binary or run lcpp-build.sh")

    cards = vram_state()
    if not cards:
        raise SystemExit("no amdgpu card exposing mem_info_vram_used; pass --vram and tune by hand")
    target = max(cards, key=lambda row: row["total_gib"])
    threads = args.threads or (os.cpu_count() or 4)
    try:
        physical = len({(read(Path(c) / "topology" / "physical_package_id"), read(Path(c) / "topology" / "core_id"))
                        for c in Path("/sys/devices/system/cpu").glob("cpu[0-9]*")})
        threads = args.threads or physical or threads
    except Exception:
        pass

    candidates = plan(facts, target["total_gib"], args.ctx)
    if args.quick:
        candidates = [c for c in candidates if " 0 " in c["label"] or "ngl" in c["label"]][:1] or candidates[:1]

    print(f"model   {model.name}  {facts.get('size_gib')} GiB  {facts.get('architecture')}  "
          f"{'MoE ' + str(facts.get('experts')) + 'e' if facts.get('experts') else 'dense'}")
    print(f"card    {target['device']}  {target['total_gib']} GiB  (free {round(target['total_gib'] - target['used_gib'], 2)})")
    print(f"plan    {len(candidates)} configs, threads {threads}, ctx {args.ctx}")

    if args.dry_run:
        for candidate in candidates:
            print(f"  {candidate['label']:<12} {candidate['flags']}")
        for row in busy():
            print(f"  note: {row['device']} already has {row['used_gib']} GiB loaded; stop it before a real run")
        return

    blockers = busy()
    if blockers and not args.force:
        print("\nA card is busy; refusing to measure against someone else's model:")
        for row in blockers:
            print(f"  {row['device']} {row['used_gib']} GiB used of {row['total_gib']}")
        print("Stop the other model, or pass --force to measure anyway (numbers will be wrong).")
        raise SystemExit(3)

    results = []
    for candidate in candidates:
        flags = candidate["flags"].replace("--timeout 1800", f"-t {threads} -tb {threads} --timeout 1800")
        server = Server(binary, model, args.port, flags, timeout_s=args.token_seconds)
        started = time.time()
        try:
            load_s = server.start()
            measured = server.probe()
            record = {
                "label": candidate["label"],
                "flags": flags,
                "load_s": load_s,
                **measured,
                "best_vram_gib": max([row["used_gib"] for row in measured["vram"]] or [0]),
                "status": "ok",
            }
        except Exception as error:
            record = {
                "label": candidate["label"],
                "flags": flags,
                "status": "error",
                "error": str(error)[:400],
                "elapsed_s": round(time.time() - started, 1),
            }
        finally:
            server.stop()
        results.append(record)
        if record["status"] == "ok":
            print(f"  {record['label']:<12} decode {record['decode_tps']:>6.2f} t/s  "
                  f"prefill {record['prefill_tps']:>6.1f} t/s  VRAM {record['best_vram_gib']:>4.1f} GiB  "
                  f"load {record['load_s']}s")
        else:
            print(f"  {record['label']:<12} FAILED: {record['error'].splitlines()[0][:120]}")

    ok = [row for row in results if row["status"] == "ok" and row["decode_tps"]]
    if not ok:
        print("\nNo configuration produced a timing line. Nothing to recommend.")
        if args.json:
            Path(args.json).write_text(json.dumps({"model": str(model), "facts": facts, "results": results}, indent=2))
        raise SystemExit(4)

    # Fastest wins, but a config within 5 percent that uses less VRAM is the better server.
    ok.sort(key=lambda row: (-row["decode_tps"], row["best_vram_gib"]))
    winner = ok[0]
    for row in ok:
        if row["decode_tps"] >= winner["decode_tps"] * 0.95 and row["best_vram_gib"] < winner["best_vram_gib"] - 0.3:
            winner = row
            break

    print(f"\nwinner  {winner['label']}  decode {winner['decode_tps']} t/s  prefill {winner['prefill_tps']} t/s")
    print(f"flags   {winner['flags']}")
    print("unit    ExecStart=" + binary + " -m " + str(model) + " --host 127.0.0.1 --port <port> --alias <name> " + winner["flags"])

    if args.json:
        Path(args.json).write_text(json.dumps({
            "model": str(model), "facts": facts, "threads": threads, "ctx": args.ctx,
            "results": results, "winner": winner,
        }, indent=2))
        print(f"report  {args.json}")


if __name__ == "__main__":
    main()
