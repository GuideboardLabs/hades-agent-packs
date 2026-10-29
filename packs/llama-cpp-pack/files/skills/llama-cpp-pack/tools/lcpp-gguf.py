#!/usr/bin/env python3
"""Read GGUF metadata without loading the model, using only the standard library.

    ./lcpp-gguf.py model.gguf            key facts + JSON
    ./lcpp-gguf.py --json model.gguf     JSON only

Why this exists: the fit math in lcpp-recommend.py needs block count, expert count,
head counts and context length, and the pack should not depend on a python package
being installed on someone else's machine. GGUF v3 stores every count as uint64,
which is the detail hand-rolled parsers get wrong and then declare a good file corrupt.
"""

import json
import struct
import sys

# value types
UINT8, INT8, UINT16, INT16, UINT32, INT32, FLOAT32, BOOL, STRING, ARRAY, UINT64, INT64, FLOAT64 = range(13)

SCALARS = {
    UINT8: ("<B", 1), INT8: ("<b", 1), UINT16: ("<H", 2), INT16: ("<h", 2),
    UINT32: ("<I", 4), INT32: ("<i", 4), FLOAT32: ("<f", 4), BOOL: ("<?", 1),
    UINT64: ("<Q", 8), INT64: ("<q", 8), FLOAT64: ("<d", 8),
}


class Reader:
    def __init__(self, handle):
        self.handle = handle

    def take(self, count):
        data = self.handle.read(count)
        if len(data) != count:
            raise ValueError("unexpected end of file")
        return data

    def scalar(self, kind):
        fmt, size = SCALARS[kind]
        return struct.unpack(fmt, self.take(size))[0]

    def string(self):
        length = struct.unpack("<Q", self.take(8))[0]
        return self.take(length).decode("utf-8", "replace")

    def value(self, kind):
        if kind in SCALARS:
            return self.scalar(kind)
        if kind == STRING:
            return self.string()
        if kind == ARRAY:
            item_kind = struct.unpack("<I", self.take(4))[0]
            length = struct.unpack("<Q", self.take(8))[0]
            return [self.value(item_kind) for _ in range(length)]
        raise ValueError(f"unknown gguf value type {kind}")


def read_meta(path):
    with open(path, "rb") as handle:
        magic = handle.read(4)
        if magic != b"GGUF":
            raise ValueError("not a GGUF file (magic mismatch)")
        version = struct.unpack("<I", handle.read(4))[0]
        tensor_count = struct.unpack("<Q", handle.read(8))[0]
        kv_count = struct.unpack("<Q", handle.read(8))[0]
        reader = Reader(handle)
        meta = {}
        for _ in range(kv_count):
            key = reader.string()
            kind = struct.unpack("<I", reader.take(4))[0]
            meta[key] = reader.value(kind)
    return {"version": version, "tensor_count": tensor_count, "meta": meta}


def facts(path_result):
    """Pull the handful of keys the fit math uses, without assuming one architecture."""
    meta = path_result["meta"]
    arch = meta.get("general.architecture", "")
    size = path_result.get("size_bytes", 0)
    prefix = f"{arch}."
    def get(name, default=None):
        for key in (prefix + name, name):
            if key in meta:
                return meta[key]
        return default
    heads_kv = get("attention.head_count_kv")
    if isinstance(heads_kv, list):
        # per-layer array: the largest value is the KV-bearing layers
        kv_heads = max([h for h in heads_kv if isinstance(h, int)] or [1])
        kv_layer_count = sum(1 for h in heads_kv if isinstance(h, int) and h > 0)
    else:
        kv_heads = heads_kv or 0
        kv_layer_count = None
    layer_types = get("attention.layer_types")
    full_layers = None
    if isinstance(layer_types, list):
        full_layers = sum(1 for t in layer_types if isinstance(t, str) and "full" in t.lower())
    return {
        "path": path_result["path"],
        "size_gib": round(size / 1024 ** 3, 2) if size else None,
        "architecture": arch,
        "name": meta.get("general.name", ""),
        "layers": get("block_count"),
        "experts": get("expert_count", 0),
        "experts_active": get("expert_used_count", 0),
        "context": get("context_length"),
        "embedding": get("embedding_length"),
        "heads": get("attention.head_count"),
        "heads_kv": kv_heads,
        "kv_layers": kv_layer_count,
        "full_attn_layers": full_layers,
        "has_mmproj_hint": any("vision" in k or "mmproj" in k for k in meta),
        "license": meta.get("general.license", ""),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("usage: lcpp-gguf.py [--json] model.gguf")
        raise SystemExit(1)
    import os
    path = args[0]
    result = read_meta(path)
    result["path"] = path
    result["size_bytes"] = os.path.getsize(path)
    summary = facts(result)
    if "--json" in sys.argv:
        print(json.dumps(summary, indent=2))
        return
    for key, value in summary.items():
        print(f"{key:>16}: {value}")


if __name__ == "__main__":
    main()
