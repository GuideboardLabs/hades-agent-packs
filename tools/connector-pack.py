#!/usr/bin/env python3
"""Pack a connector crate into an installable connector pack.

A connector pack is a tar.gz holding `connector.json`, a `bin/` tree and a `SHA256SUMS` that covers
every file except itself. This script builds the binary from `packs/<id>/`, writes the manifest,
hashes the tree and prints the archive path. It refuses to pack a binary it did not just build.

The crate lives beside the pack it produces, so the source of a connector and the thing a user
installs are in the same repository and version together. `files/connectors/<id>/` is regenerated on
every run; `tools/build.py` then wraps it into the `.hadepack` the shelf lists.

Usage: python3 tools/connector-pack.py [--id discord] [--out dist]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "packs"


def crate_version(manifest: Path) -> str:
    """Read the version from the crate itself.

    Hardcoding it here is how the pack stayed at 0.1.1 while the runtime shipped 0.3.0, which left
    the release installed nowhere. One source of truth: the manifest cargo builds from.
    """
    for line in manifest.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "version":
            return value.strip().strip('"')
    raise SystemExit(f"connector-pack: no version in {manifest}")


def required_hades(manifest: Path) -> str:
    """The base version the pack was built against, read from the pinned git tag.

    The tag is the only honest source: it is what cargo resolved, so the manifest and the binary
    cannot disagree about which runtime they were compiled for.
    """
    match = re.search(
        r'harness\s*=\s*\{[^}]*tag\s*=\s*"v?([0-9][^"]*)"',
        manifest.read_text(),
    )
    if not match:
        raise SystemExit(f"connector-pack: no harness tag in {manifest}")
    return match.group(1)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default="discord")
    parser.add_argument("--out", default="dist")
    parser.add_argument("--profile", default="release")
    args = parser.parse_args()

    crate = PACKS / args.id
    version = crate_version(crate / "Cargo.toml")
    base = required_hades(crate / "Cargo.toml")

    target = "release" if args.profile == "release" else "debug"
    env = dict(os.environ)
    env.setdefault("CARGO_TARGET_DIR", str(ROOT / "target"))
    build = subprocess.run(
        [
            env.get("CARGO", "cargo"),
            "build",
            "-p",
            "hades-connector-discord",
            *(["--release"] if target == "release" else []),
        ],
        cwd=ROOT,
        env=env,
    )
    if build.returncode != 0:
        print("cargo build failed", file=sys.stderr)
        return 1

    binary = Path(env["CARGO_TARGET_DIR"]) / target / "hades-connector-discord"
    if not binary.is_file():
        print(f"no binary at {binary}", file=sys.stderr)
        return 1

    # The staged tree is what `hades pack add` unpacks. It is built in `build/`, then archived into
    # `files/connectors/<id>/` as a single tar.gz, because that is what the shelf validates and what
    # the runtime installs: a pack holds the connector's archive, not its unpacked tree.
    name = f"hades-connector-{args.id}-{version}"
    stage = ROOT / "build" / name
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "bin").mkdir(parents=True)
    shutil.copy2(binary, stage / "bin" / "hades-connector-discord")
    (stage / "bin" / "hades-connector-discord").chmod(0o755)
    # The pack's own words and its own animation live here, not in the harness: a connector that is
    # fully removable has to carry what it says.
    prompt = crate / "prompt.md"
    if prompt.is_file():
        shutil.copy2(prompt, stage / "prompt.md")
    for extra in ("LICENSE", "NOTICE"):
        source = ROOT / extra
        if source.is_file():
            shutil.copy2(source, stage / extra)

    manifest = {
        "format": 1,
        "kind": "connector",
        "id": args.id,
        "version": version,
        "title": "Discord",
        "summary": "Discord gateway connector for Hades: DMs and guild mentions, approvals as buttons.",
        "license": "MIT",
        "protocol": {"min": 1, "max": 1},
        "requires_hades": {"min": base},
        "platform": "linux-x86_64",
        "entrypoint": "bin/hades-connector-discord",
        "prompt": "prompt.md",
        "loading": {"cells": "💀", "mark": "☠️", "span": 5, "step_ms": 2000, "glyph": "💀"},
        "config_schema": {
            "allowed_users": {"type": "array<string>", "required": True},
            "allowed_guilds": {"type": "array<string>", "default": []},
            "require_mention": {"type": "boolean", "default": True},
            "model": {
                "type": "string",
                "optional": True,
                "note": "defaults to the gateway's model; name one only to route this surface elsewhere",
            },
            "provider": {
                "type": "object",
                "optional": True,
                "note": "not needed to install: the gateway's provider is used unless this is set",
            },
            "prompt": {"type": "string", "note": "replaces the pack's prompt.md"},
            "loading": {"type": "object", "note": "cells, mark, span, step_ms, glyph"},
            "operators": {"type": "array<string>", "note": "who may do more than talk"},
            "channels": {"type": "object", "note": "per-channel mode/policy/root/model/reasoning"},
            "reasoning": {"type": "string", "note": "off, low, medium or high"},
        },
        "signature": None,
    }
    (stage / "connector.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (stage / "README.md").write_text(
        "# Hades Discord connector\n\n"
        "A surface pack for Hades. Install it with `hades pack add <this.tar.gz>` and run it with\n"
        "`hades connector run discord --config <config>`. The token is read from\n"
        "`HADES_CONNECTOR_DISCORD_TOKEN`. The surface answers on the gateway's model and provider,\n"
        "the same ones the rest of the install uses; `gateway.connectors.discord` only has to say\n"
        "who may talk to it and where its channels are.\n"
    )

    sums = []
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            sums.append(f"{digest(path)}  {path.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n")

    # Two archives on purpose: the one the pack carries, and a copy in dist/ for a person who wants
    # to install this connector without the shelf. Same bytes, built once.
    carried = crate / "files" / "connectors" / args.id
    carried.mkdir(parents=True, exist_ok=True)
    for stale in carried.glob("*.tar.gz"):
        stale.unlink()
    inside = carried / f"{name}-linux-x86_64.tar.gz"
    subprocess.run(["tar", "-czf", str(inside), "-C", str(stage.parent), name], check=True)

    archive = Path(args.out) / inside.name
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    shutil.copy2(inside, archive)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
