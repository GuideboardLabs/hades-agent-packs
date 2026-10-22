#!/usr/bin/env python3
"""Build the pack menu from the pack directories.

One source of truth: `packs/<id>/pack.json` and the files beside it. This script writes three things and
never the reverse:

    dist/<id>-<version>.pack   the archive `hades pack add` fetches
    INDEX.json                 what a program reads
    MENU.md                    what a person reads; generated, do not edit

Usage:
    tools/build.py                 write dist/, INDEX.json and MENU.md
    tools/build.py --check         exit 1 if the committed index is stale (for CI)
    tools/build.py --inspect BIN   also run `<BIN> pack inspect` on every archive
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / "packs"
DIST = ROOT / "dist"
INDEX = ROOT / "INDEX.json"
MENU = ROOT / "MENU.md"

FORMAT = 1
KEYS = {
    "format", "id", "version", "title", "summary", "license", "derivatives",
    "requires", "provides", "capabilities", "keywords",
}
KINDS = {"skills": "skill", "harnesses": "tool", "cases": "test"}
FORBIDDEN = {"roles", "floor", "cage", "policy"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_manifest(pack_dir: Path, manifest: dict) -> list[str]:
    """The same rules the runtime enforces, checked before an archive exists."""
    problems = []
    unknown = set(manifest) - KEYS
    if unknown:
        problems.append(f"unknown keys: {sorted(unknown)}")
    for bad in FORBIDDEN & set(manifest):
        problems.append(f"guardrails are not pack content: `{bad}`")
    if manifest.get("format") != FORMAT:
        problems.append(f"format must be {FORMAT}")
    if manifest.get("id") != pack_dir.name:
        problems.append(f"id `{manifest.get('id')}` does not match the directory `{pack_dir.name}`")
    for field in ("version", "title", "summary", "license", "derivatives"):
        if not isinstance(manifest.get(field), str) or not manifest.get(field).strip():
            problems.append(f"`{field}` must be a non-empty string")
    if manifest.get("derivatives") not in ("allowed", "forbidden"):
        problems.append("`derivatives` must be `allowed` or `forbidden`")
    keywords = manifest.get("keywords", [])
    if not isinstance(keywords, list) or not all(
        isinstance(k, str) and k.strip() for k in keywords
    ):
        problems.append("`keywords` must be a list of non-empty strings")
    requires = manifest.get("requires") or {}
    if set(requires) != {"hades", "roles", "tools"}:
        problems.append("`requires` must have exactly hades, roles, tools")
    if not isinstance(requires.get("hades"), str) or not requires["hades"].strip():
        problems.append("`requires.hades` must be a range, for example >=0.1.0,<0.2")
    provides = manifest.get("provides") or {}
    if set(provides) != set(KINDS):
        problems.append("`provides` must have exactly skills, harnesses, cases")
    items = []
    for key, kind in KINDS.items():
        for entry in provides.get(key, []):
            name = entry.get("name") or entry.get("profile") or entry.get("id")
            where = entry.get("path", "")
            expected = f"files/{key}/{name}"
            if where != expected:
                problems.append(f"provides.{key} `{name}` path must be `{expected}`")
            folder = pack_dir / where
            if not folder.is_dir():
                problems.append(f"provides.{key} `{name}` has no folder at {where}")
            elif not any(folder.rglob("*")):
                problems.append(f"provides.{key} `{name}` is empty")
            items.append({"kind": kind, "name": name})
    if not items:
        problems.append("a pack that provides nothing is not a pack")
    capabilities = manifest.get("capabilities") or {}
    if set(capabilities) != {"network", "writes", "code"}:
        problems.append("`capabilities` must have exactly network, writes, code")
    if capabilities.get("code") is not False:
        problems.append("`capabilities.code` must be false")
    writes = capabilities.get("writes", [])
    if not isinstance(writes, list) or any(w not in ("vault", "state") for w in writes):
        problems.append("`capabilities.writes` may only name `vault` or `state`")
    return problems


def build_archive(pack_dir: Path, manifest: dict) -> tuple[Path, bytes]:
    """The archive layout the runtime reads: pack.json and files/, with a sums file beside them."""
    staging = Path(tempfile.mkdtemp(prefix="pack-"))
    try:
        shutil.copy2(pack_dir / "pack.json", staging / "pack.json")
        shutil.copytree(pack_dir / "files", staging / "files")
        sums = []
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                rel = path.relative_to(staging).as_posix()
                sums.append(f"{digest(path.read_bytes())}  {rel}")
        (staging / "SHA256SUMS").write_text("\n".join(sums) + "\n")
        DIST.mkdir(exist_ok=True)
        out = DIST / f"{manifest['id']}-{manifest['version']}.hadepack"
        # Byte-for-byte the archive `hades pack export` writes: sorted entries, epoch mtime, owner 0, and
        # `gzip -n` so the compression header carries no timestamp. Without that, the same pack built twice
        # gets two digests and every menu row goes stale on the next build.
        with open(out, "wb") as sink:
            tar = subprocess.Popen(
                [
                    "tar",
                    "--sort=name",
                    "--mtime=@0",
                    "--owner=0",
                    "--group=0",
                    "--numeric-owner",
                    "-C",
                    str(staging),
                    "-cf",
                    "-",
                    ".",
                ],
                stdout=subprocess.PIPE,
                env={**os.environ, "COPYFILE_DISABLE": "1"},
            )
            gzip = subprocess.Popen(
                ["gzip", "-n"], stdin=tar.stdout, stdout=sink
            )
            if tar.stdout is not None:
                tar.stdout.close()
            gzip.wait()
            tar.wait()
            if tar.returncode or gzip.returncode:
                raise SystemExit(f"packaging {out.name} failed")
        return out, out.read_bytes()
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def collect() -> tuple[list[dict], list[str]]:
    rows, problems = [], []
    for pack_dir in sorted(p for p in PACKS.iterdir() if p.is_dir()):
        manifest_path = pack_dir / "pack.json"
        if not manifest_path.is_file():
            problems.append(f"{pack_dir.name}: no pack.json")
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError as error:
            problems.append(f"{pack_dir.name}: pack.json is not JSON: {error}")
            continue
        found = check_manifest(pack_dir, manifest)
        if found:
            problems.extend(f"{pack_dir.name}: {p}" for p in found)
            continue
        archive, data = build_archive(pack_dir, manifest)
        items = [
            {"kind": KINDS[key], "name": entry.get("name") or entry.get("profile") or entry.get("id")}
            for key in KINDS
            for entry in manifest["provides"][key]
        ]
        rows.append({
            "id": manifest["id"],
            "version": manifest["version"],
            "title": manifest["title"],
            "summary": manifest["summary"],
            "license": manifest["license"],
            "derivatives": manifest["derivatives"],
            "keywords": manifest.get("keywords", []),
            "kind": sorted({item["kind"] for item in items}),
            "items": items,
            "requires_hades": manifest["requires"]["hades"],
            "capabilities": manifest["capabilities"],
            "bytes": len(data),
            "sha256": digest(data),
            "archive": f"dist/{archive.name}",
        })
    return rows, problems


def index_text(rows: list[dict]) -> str:
    return json.dumps({
        "menu": 1,
        "note": "Generated by tools/build.py from packs/*/pack.json. Do not edit.",
        "packs": rows,
    }, indent=2, sort_keys=True) + "\n"


def menu_text(rows: list[dict]) -> str:
    lines = [
        "# Pack menu",
        "",
        "The shelf Hades checks before it writes a skill of its own. Generated by `tools/build.py` from",
        "`packs/*/pack.json`; do not edit this file.",
        "",
        "Every row is a pack this repository publishes. A pack is data: a skill, a tool profile, or a test",
        "case, with a manifest that says what it needs and what it is allowed to touch.",
        "",
        "| pack | kind | version | what it does | keywords | size | sha256 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda r: r["id"]):
        kinds = ", ".join(sorted({i["kind"] for i in row["items"]}))
        lines.append(
            f"| `{row['id']}` | {kinds} | {row['version']} | {row['summary']} | "
            f"{', '.join(row['keywords'])} | {human(row['bytes'])} | `{row['sha256'][:12]}` |"
        )
    lines += [
        "",
        "## Using a pack",
        "",
        "```",
        "hades pack menu                       # this table, from the cached index",
        "hades pack find review                # match id, title, summary or keywords",
        "hades pack add review-a-diff          # fetch, verify sha256, print the plan, install",
        "```",
        "",
        "`add` verifies the archive against the sha256 in `INDEX.json` before it reads the manifest, and",
        "prints what it would write before it writes anything. `--yes` skips the question, not the checks.",
        "",
        "## Writing a pack",
        "",
        "```",
        "cp -r TEMPLATE packs/<id>             # pack.json plus files/skills/<id>/SKILL.md",
        "tools/build.py                        # validates, archives, regenerates this menu",
        "```",
        "",
        "The manifest rules are in `README.md`. A pack that fails them is not written to `dist/` at all.",
        "",
    ]
    return "\n".join(lines)


def human(n: int) -> str:
    return f"{n / 1024:.1f} kB" if n >= 1024 else f"{n} B"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the committed index is stale")
    parser.add_argument("--inspect", metavar="BIN", help="run `<BIN> pack inspect` on each archive")
    parser.add_argument(
        "--ship", metavar="REPO",
        help="also copy INDEX.json and MENU.md into a Hades checkout's docs/packs/ (release step)",
    )
    args = parser.parse_args()

    rows, problems = collect()
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    stale = []
    if INDEX.is_file() and INDEX.read_text() != index_text(rows):
        stale.append("INDEX.json")
    if MENU.is_file() and MENU.read_text() != menu_text(rows):
        stale.append("MENU.md")

    if args.check:
        if stale:
            print(f"stale: {', '.join(stale)}; run tools/build.py", file=sys.stderr)
            return 1
        print(f"ok: {len(rows)} packs, index current")
        return 0

    INDEX.write_text(index_text(rows))
    MENU.write_text(menu_text(rows))
    print(f"wrote dist/, INDEX.json and MENU.md for {len(rows)} packs")

    if args.ship:
        target = Path(args.ship) / "docs" / "packs"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(INDEX, target / "INDEX.json")
        shutil.copy2(MENU, target / "MENU.md")
        print(f"shipped the menu to {target}")

    if args.inspect:
        for row in rows:
            result = subprocess.run(
                [args.inspect, "pack", "inspect", str(ROOT / row["archive"])],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"error: {row['id']}: {result.stderr.strip() or result.stdout.strip()}",
                      file=sys.stderr)
                return 1
        print(f"inspected {len(rows)} archives with {args.inspect}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
