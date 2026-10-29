#!/usr/bin/env python3
"""Recommend which model to run on this machine, and with which flags.

    ./lcpp-recommend.py                      detect, scan ~/models and /mnt/*/models, rank
    ./lcpp-recommend.py --scan /data/gguf    add a directory of your own
    ./lcpp-recommend.py --ctx 32768          ask for a specific context
    ./lcpp-recommend.py --detect-json r.json use a saved lcpp-detect.py report
    ./lcpp-recommend.py --downloads          also list catalog models you do not have
    ./lcpp-recommend.py --json               machine readable

Two kinds of number appear here and they are not the same kind of thing:

* the fit math is arithmetic on a real file size and real GGUF metadata, so "fits" is a
  claim this script can stand behind;
* the speed expectation only appears when a measurement exists for that exact model on
  hardware close to yours. Otherwise it says "unmeasured" and points at lcpp-tune.py.

Exit code 1 when nothing fits, so a script can branch on it.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import importlib.util

spec = importlib.util.spec_from_file_location("lcpp_gguf", HERE / "lcpp-gguf.py")
gguf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gguf)

GIB = 1024 ** 3
# Fraction of layers that carry full attention. Hybrid architectures keep O(1) state on the
# rest, which is why their KV is small. These are approximations; the recommender says so and
# tells you to confirm with the server's n_ctx and VRAM.
HYBRID_FRACTION = {
    "qwen35moe": 0.25,
    "qwen3next": 0.25,
    "clover_moe": 0.30,
    "lfm3": 0.50,
    "minicpmv": 0.25,
    "nemotron_h": 0.10,
}
KV_BYTES_PER_ELEMENT = {"f16": 2.0, "q8_0": 1.0, "q4_0": 0.56}
CTX_LADDER = [131072, 65536, 32768, 16384, 8192, 4096, 2048]


def detect():
    report = subprocess.run(
        [sys.executable, str(HERE / "lcpp-detect.py"), "--json"],
        capture_output=True, text=True, timeout=60,
    )
    if report.returncode != 0:
        print("detect failed:\n" + report.stderr, file=sys.stderr)
        raise SystemExit(2)
    return json.loads(report.stdout)


def hardware(report, override_vram=None, override_ram=None):
    gpus = report.get("gpus", [])
    best = None
    for gpu in gpus:
        vram = gpu.get("vram_gib") or 0
        if best is None or vram > (best.get("vram_gib") or 0):
            best = gpu
    vram = override_vram or (best or {}).get("vram_gib") or 0.0
    ram = override_ram or report.get("memory", {}).get("ram_gib") or 0.0
    physical = report.get("cpu", {}).get("physical") or os.cpu_count() or 1
    return {
        "gpu": f"{best.get('vendor','?')} {best.get('name','?')}" if best else "no GPU",
        "vram": float(vram),
        "ram": float(ram),
        "threads": int(physical),
        "label": f"{best.get('name','CPU')} {vram:.1f} GB / {physical}c / {ram:.0f} GB RAM" if best
                 else f"CPU only / {physical}c / {ram:.0f} GB RAM",
    }


def file_facts(path):
    try:
        result = gguf.read_meta(path)
        result["path"] = str(path)
        result["size_bytes"] = os.path.getsize(path)
        facts = gguf.facts(result)
        facts["size_gib"] = round(facts["size_gib"] or result["size_bytes"] / GIB, 2)
        return facts
    except Exception as error:
        return {"path": str(path), "error": str(error)}


def quant_from_name(name):
    upper = name.upper()
    for token in ("IQ1", "IQ2", "IQ3", "IQ4", "Q2_K", "Q3_K", "Q4_K", "Q5_K", "Q6_K",
                  "Q8_0", "Q4_0", "Q5_0", "BF16", "F16", "TQ2_0", "PQ2_0"):
        if token.replace("_", "") in upper.replace("_", "").replace("-", ""):
            return token
    return "unknown"


def kv_gib(facts, ctx, kv_type, arch):
    heads = facts.get("heads") or 32
    heads_kv = facts.get("heads_kv") or heads
    embedding = facts.get("embedding") or 4096
    layers = facts.get("layers") or 32
    head_dim = embedding / heads if heads else 128
    full = facts.get("full_attn_layers")
    if not full:
        fraction = HYBRID_FRACTION.get((arch or "").lower())
        if fraction is None:
            fraction = 0.5 if (facts.get("experts") or 0) else 1.0
        full = max(1, round(layers * fraction))
    element = KV_BYTES_PER_ELEMENT.get(kv_type, 2.0)
    return 2 * heads_kv * head_dim * full * element * ctx / GIB


def assess(candidate, hw, want_ctx, overhead):
    facts = candidate["facts"]
    size = facts.get("size_gib") or candidate.get("size_gib") or 0
    arch = facts.get("architecture") or candidate.get("arch") or ""
    layers = facts.get("layers") or 32
    moe = bool(facts.get("experts") or candidate.get("moe"))
    usable = hw["vram"] * 0.94 if hw["vram"] else 0.0
    ctx_limit = facts.get("context") or candidate.get("context") or 32768

    MIN_CTX = 8192  # below this a model technically fits and is practically useless
    chosen = None
    for kv_type in ("q8_0", "q4_0"):
        for ctx in CTX_LADDER:
            if ctx < MIN_CTX or ctx > min(ctx_limit, want_ctx):
                continue
            need = size + kv_gib(facts, ctx, kv_type, arch) + overhead
            if usable and need <= usable:
                chosen = (ctx, kv_type, need)
                break
        if chosen:
            break

    if chosen:
        ctx, kv_type, need = chosen
        kv = kv_gib(facts, ctx, kv_type, arch)
        return {
            "fit": "full",
            "ctx": ctx,
            "kv_type": kv_type,
            "kv_gib": round(kv, 2),
            "need_gib": round(need, 2),
            "flags": f"-ngl 999 -fa on -c {ctx} -ctk {kv_type} -ctv {kv_type} -t {hw['threads']} -tb {hw['threads']} --timeout 1800",
            "offload": None,
        }

    if moe and usable:
        ctx, kv_type = min(8192, ctx_limit), "q8_0"
        kv = kv_gib(facts, ctx, kv_type, arch)
        attention = size * 0.15
        room_for_experts = max(0.0, usable - kv - overhead - attention)
        experts = size * 0.85
        keep_fraction = min(1.0, room_for_experts / experts) if experts else 0.0
        ncmoe = max(0, min(layers, round(layers * (1 - keep_fraction))))
        need = attention + experts * keep_fraction + kv + overhead
        return {
            "fit": "offload",
            "ctx": ctx,
            "kv_type": kv_type,
            "kv_gib": round(kv, 2),
            "need_gib": round(need, 2),
            "flags": f"-ngl 999 -ncmoe {ncmoe} -fa on -c {ctx} -ctk {kv_type} -ctv {kv_type} -t {hw['threads']} -tb {hw['threads']} --load-mode none --timeout 1800",
            "offload": {"ncmoe": ncmoe, "sweep": f"sweep {max(0,ncmoe-4)} to {min(layers,ncmoe+8)} in steps of 4; the cliff is sharp"},
        }

    if usable and not moe:
        # Dense model: spend GPU layers instead of experts.
        ctx, kv_type = MIN_CTX, "q8_0"
        kv = kv_gib(facts, ctx, kv_type, arch)
        room = max(0.0, usable - kv - overhead)
        fraction = min(1.0, room / size) if size else 0.0
        ngl = max(0, min(layers, round(layers * fraction)))
        return {
            "fit": "partial",
            "ctx": ctx,
            "kv_type": kv_type,
            "kv_gib": round(kv, 2),
            "need_gib": round(size * fraction + kv + overhead, 2),
            "flags": f"-ngl {ngl} -fa on -c {ctx} -ctk {kv_type} -ctv {kv_type} -t {hw['threads']} -tb {hw['threads']} --timeout 1800",
            "offload": {"ngl": ngl, "sweep": f"-ngl {ngl} is the fit estimate; sweep {max(0,ngl-6)} to {min(layers,ngl+6)}"},
        }

    if size + overhead <= hw["ram"] * 0.85:
        return {
            "fit": "ram",
            "ctx": min(4096, ctx_limit),
            "kv_type": "q8_0",
            "kv_gib": round(kv_gib(facts, min(4096, ctx_limit), "q8_0", arch), 2),
            "need_gib": round(size + overhead, 2),
            "flags": "-ngl 0 -c 4096 -t " + str(hw["threads"]),
            "offload": None,
        }

    return {"fit": "no", "ctx": 0, "kv_type": "", "kv_gib": 0, "need_gib": round(size, 2), "flags": "", "offload": None}


def measured_line(candidate, entry):
    rows = []
    for row in (entry or {}).get("measured", []):
        bits = []
        if row.get("decode_tps"):
            bits.append(f"{row['decode_tps']} t/s decode")
        if row.get("prefill_tps"):
            bits.append(f"{row['prefill_tps']} t/s prefill")
        if row.get("seconds_per_image"):
            bits.append(f"{row['seconds_per_image']} s/image")
        where = row.get("vram_gib")
        if where:
            bits.append(f"{where} GB VRAM")
        rows.append(f"{row['hardware']}: " + ", ".join(bits))
    return "; ".join(rows)


def walk_ggufs(root, depth=2):
    """Depth-limited walk: a full rglob on a slow USB library takes minutes for nothing."""
    root = Path(root)
    stack = [(root, 0)]
    found = 0
    while stack and found < 200:
        current, level = stack.pop()
        try:
            entries = list(os.scandir(current))
        except Exception:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if level + 1 <= depth:
                        stack.append((Path(entry.path), level + 1))
                elif entry.name.lower().endswith(".gguf") and not entry.name.lower().startswith("mmproj"):
                    found += 1
                    yield Path(entry.path)
            except Exception:
                continue


CHOICE_ROWS = [
    {
        "title": "Autocomplete and instant answers",
        "why": "the first token lands inside a keystroke budget; depth is wasted here",
        "rule": "speed unless the completion has to be right about facts, then balanced",
        "seats": [("speed", "qwen2.5-0.5b-instruct-q4_k_m"),
                  ("balanced", "minicpm5-2b-q4_k_m"),
                  ("depth", "qwen3.8-9b-q4_k_m")],
    },
    {
        "title": "General chat",
        "why": "one conversation at a time, answers read by a human",
        "rule": "balanced is the default; depth when the question needs planning",
        "seats": [("speed", "minicpm5-2b-q4_k_m"),
                  ("balanced", "qwen3.8-9b-q4_k_m"),
                  ("depth", "qwen3.8-35b-a3b-distill-q4_k_m")],
    },
    {
        "title": "Code edits",
        "why": "answers copy long spans out of the prompt, so drafting and a bigger model pay more than raw decode",
        "rule": "balanced for edits inside one file; depth when the change spans files",
        "seats": [("speed", "qwen3.8-9b-q4_k_m"),
                  ("balanced", "qwen3.8-35b-a3b-distill-q4_k_m"),
                  ("depth", "flint-40b-a3b-q4_k_xl")],
    },
    {
        "title": "Long-context reading",
        "why": "decode falls as the KV cache grows, so the small model takes the least damage",
        "rule": "speed for search and extract; balanced for summarising a whole file",
        "seats": [("speed", "minicpm5-2b-q4_k_m"),
                  ("balanced", "qwen3.8-9b-q4_k_m"),
                  ("depth", "clover-35b-a3b-q4_0")],
    },
    {
        "title": "Vision",
        "why": "the projector decides the cost, not the language model behind it",
        "rule": "speed keeps the projector on CPU and VRAM at zero; depth when the image is the question",
        "seats": [("speed", "qwen3vl-4b", "-ngl 0 -c 32768 -t 6 -tb 6"),
                  ("balanced", "local:Qwen3Vl 8b"),
                  ("depth", "minicpm-v-4.7-35b-a3b")],
    },
    {
        "title": "Hard problems, low volume",
        "why": "one answer a minute is fine if the answer is right",
        "rule": "balanced for arithmetic and code reasoning; depth for open-ended design",
        "seats": [("speed", "qwen3.8-9b-q4_k_m"),
                  ("balanced", "qwen3.8-27b-iq3_xxs"),
                  ("depth", "nanbeige4.3-33b-q4_k_m")],
    },
]


def choice_index(candidates):
    by_key, by_name = {}, {}
    for candidate in candidates:
        key = (candidate["entry"] or {}).get("key")
        if key:
            by_key[key] = candidate
        by_name[candidate["name"].lower()] = candidate
        by_name[Path((candidate["facts"] or {}).get("path") or candidate["name"]).name.lower()] = candidate
    return by_key, by_name


def seat_candidate(seat, by_key, by_name):
    if seat.startswith("local:"):
        wanted = seat.split(":", 1)[1].lower()
        for name, candidate in by_name.items():
            if wanted in name:
                return candidate
        return None
    return by_key.get(seat)


def print_choice(candidates, hw, args):
    by_key, by_name = choice_index(candidates)
    print(f"hardware: {hw['label']}")
    print("three seats per row: speed buys latency, depth buys the harder answer")
    print()
    missing = 0
    for row in CHOICE_ROWS:
        print(row["title"])
        print(f"  why        {row['why']}")
        for seat_row in row["seats"]:
            label, seat = seat_row[0], seat_row[1]
            override = seat_row[2] if len(seat_row) > 2 else None
            candidate = seat_candidate(seat, by_key, by_name)
            if candidate is None:
                missing += 1
                print(f"  {label:<10} not present on this machine and not in the catalog")
                continue
            verdict = candidate["verdict"]
            entry = candidate["entry"] or {}
            flags = override or verdict["flags"] or "not fitting"
            speed = measured_line(candidate, entry) or "unmeasured here"
            where = "" if candidate["present"] else " [download]"
            print(f"  {label:<10} {candidate['name'][:34]:<34} {candidate['size_gib'] or 0:6.2f} GiB  "
                  f"{verdict['fit'].upper():<7} ctx {verdict['ctx']:<6} {speed}{where}")
            print(f"  {'':<10} {flags[:118]}")
        print(f"  rule       {row['rule']}")
        print()
    if missing:
        print(f"{missing} seat(s) could not be filled on this machine. The sheet is complete only when its")
        print("files are present: run with --downloads to see the ones the catalog would fetch.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="append", default=[], help="directory of .gguf files")
    parser.add_argument("--ctx", type=int, default=32768)
    parser.add_argument("--overhead", type=float, default=0.6, help="GiB for compute buffers and the desktop")
    parser.add_argument("--vram", type=float, default=None, help="override detected VRAM in GiB")
    parser.add_argument("--ram", type=float, default=None, help="override detected RAM in GiB")
    parser.add_argument("--detect-json", default=None)
    parser.add_argument("--catalog", default=str(HERE / "catalog.json"))
    parser.add_argument("--downloads", action="store_true", help="also rank catalog models not present locally")
    parser.add_argument("--all", action="store_true", help="include entries that do not fit")
    parser.add_argument("--choice", action="store_true", help="six rows, three seats each: speed against depth")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = json.load(open(args.detect_json)) if args.detect_json else detect()
    hw = hardware(report, args.vram, args.ram)

    catalog = {}
    if Path(args.catalog).exists():
        for entry in json.load(open(args.catalog)).get("models", []):
            catalog[entry["file"]] = entry

    roots = [Path(p).expanduser() for p in args.scan] or [
        Path.home() / "models",
        Path("/mnt/ssd/models"),
        Path("/mnt/blue"),
        Path("/home/sc/models-clover"),
        Path("/home/sc/vision-models"),
    ]
    files, seen = [], set()
    for root in roots:
        if not root.exists():
            continue
        for path in walk_ggufs(root, depth=2):
            try:
                key = (path.name, os.path.getsize(path))
            except Exception:
                continue
            if path.name.lower().startswith("mmproj"):
                continue
            if key in seen:
                continue
            seen.add(key)
            files.append(path)

    candidates = []
    for path in files:
        facts = file_facts(path)
        if "error" in facts:
            continue
        entry = catalog.get(path.name, {})
        raw_name = (facts.get("name") or "").strip()
        # Some GGUFs carry a training config blob as general.name; prefer the file stem then.
        if not entry.get("name") and (len(raw_name) > 40 or " L1" in raw_name or raw_name.lower() in ("ours", "")):
            raw_name = path.name.rsplit(".", 1)[0]
        candidates.append({
            "name": entry.get("name") or raw_name or path.name,
            "facts": facts,
            "entry": entry,
            "size_gib": facts.get("size_gib"),
            "present": True,
        })

    if args.downloads:
        present = {c["facts"].get("path") for c in candidates}
        for entry in catalog.values():
            if entry.get("file") and not any(entry["file"] == Path(p).name for p in present if p):
                candidates.append({
                    "name": entry.get("name", entry["file"]),
                    "facts": {"size_gib": entry.get("size_gib"), "architecture": entry.get("arch"),
                              "experts": 1 if entry.get("moe") else 0, "layers": entry.get("layers"),
                              "context": entry.get("context"), "size_gib_is_estimate": True},
                    "entry": entry,
                    "size_gib": entry.get("size_gib"),
                    "present": False,
                })

    for candidate in candidates:
        candidate["verdict"] = assess(candidate, hw, args.ctx, args.overhead)

    order = {"full": 0, "offload": 1, "partial": 2, "ram": 3, "no": 4}
    def sort_key(candidate):
        verdict = candidate["verdict"]
        measured = (candidate["entry"] or {}).get("measured") or []
        speed = max([m.get("decode_tps") or 0 for m in measured] or [0])
        params = (candidate["entry"] or {}).get("params_b") or 0
        return (order[verdict["fit"]], -speed, -params)
    candidates.sort(key=sort_key)

    if args.choice:
        print_choice(candidates, hw, args)
        return

    if args.json:
        print(json.dumps({"hardware": hw, "candidates": [
            {k: v for k, v in c.items() if k != "facts"} | {"facts": {kk: vv for kk, vv in c["facts"].items() if kk != "path"}}
            for c in candidates
        ]}, indent=2, default=str))
        return

    print(f"hardware: {hw['label']}")
    print(f"target context: {args.ctx}   overhead budget: {args.overhead} GiB")
    print()
    printed = 0
    for candidate in candidates:
        verdict = candidate["verdict"]
        if verdict["fit"] == "no" and not args.all:
            continue
        printed += 1
        size = candidate["size_gib"] or 0
        facts = candidate["facts"]
        arch = facts.get("architecture") or "?"
        experts = facts.get("experts") or 0
        moe = f"MoE {experts}e" if experts else "dense"
        where = "" if candidate["present"] else "  [not local: download]"
        print(f"{candidate['name']}{where}")
        print(f"  file        {size:.2f} GiB  {arch}  {moe}  ctx<= {facts.get('context') or '?'}")
        print(f"  verdict     {verdict['fit'].upper()}  need ~{verdict['need_gib']} GiB  ctx {verdict['ctx']}  kv {verdict['kv_gib']} GiB ({verdict['kv_type'] or 'n/a'})")
        if verdict["flags"]:
            print(f"  flags       {verdict['flags']}")
        if verdict["offload"]:
            print(f"  offload     {verdict['offload']['sweep']}")
        line = measured_line(candidate, candidate["entry"])
        print(f"  measured    {line}" if line else "  measured    nothing on this hardware; run lcpp-tune.py")
        note = (candidate["entry"] or {}).get("notes")
        if note:
            print(f"  note        {note}")
        print()

    if not printed:
        print("Nothing fits. Options: a smaller quant, a smaller model, or accept RAM-only at a few t/s.")
        print("Run with --all to see what was rejected and by how much.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
