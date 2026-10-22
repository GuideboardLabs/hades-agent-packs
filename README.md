# Packs

Skills, tool profiles and test cases for [Hades](https://github.com/GuideboardLabs/hades-agent), kept out of
the runtime so the runtime stays small. Hades ships `MENU.md` and `INDEX.json` as a reference: it knows what
exists, and installs only what a person asks for.

Every pack is data. It carries a manifest that says what it provides, what it needs, and what it is allowed
to touch. Nothing here can declare a guardrail, a role or a cage rule; those belong to the runtime, not to a
download.

This repository and the packs in it are MIT. A pack installed into Hades is not covered by Hades' licence and
Hades' licence does not reach it: a pack's `license` field is the whole statement. A commercial pack says so,
sets `derivatives: forbidden`, and publishes its archive somewhere that is not this public repository; the
menu row may name an absolute `https://` archive URL, and `hades pack add` checks the same digest either
way.

## The two rules

1. **The menu is checked before a new skill is written.** A skill that already exists here is installed, not
   reinvented. That is why `keywords` exists in the manifest: it is what `hades pack find` matches.
2. **A pack is installed by a person, not by a model.** An agent may read the menu and propose a pack; the
   install is a command with a printed plan. This is the same boundary as everywhere else in Hades: the
   thing that decides is not the thing that acts.

## Layout

```
MENU.md                    generated, for people and agents
INDEX.json                 generated, for programs
dist/<id>-<version>.hadepack   generated, the archive that is fetched
packs/<id>/pack.json       the manifest, written by hand
packs/<id>/files/...       the payload, laid out exactly as the manifest names it
tools/build.py             validates, archives, regenerates the menu
TEMPLATE/                  a pack with nothing in it, to copy
```

`MENU.md` and `INDEX.json` are generated from `packs/`. Do not edit them by hand; `tools/build.py --check`
fails in CI when they are stale.

## Adding a pack

```bash
cp -r TEMPLATE packs/my-pack
$EDITOR packs/my-pack/pack.json packs/my-pack/files/skills/my-pack/SKILL.md
tools/build.py                 # validates, writes dist/, regenerates MENU.md and INDEX.json
tools/build.py --check         # what CI runs
```

If `tools/build.py` prints an error, the pack is not written to `dist/` at all. The checks are the same ones
the runtime applies, so a pack that builds is a pack `hades pack inspect` accepts.

## The manifest

```json
{
  "format": 1,
  "id": "my-pack",
  "version": "1.0.0",
  "title": "One line, for a menu",
  "summary": "What it does, in one sentence.",
  "license": "MIT",
  "derivatives": "allowed",
  "keywords": ["three", "to", "six", "words"],
  "requires": { "hades": ">=0.1.0,<0.2", "roles": [], "tools": ["read_file"] },
  "provides": {
    "skills":    [{ "name": "my-pack", "path": "files/skills/my-pack" }],
    "harnesses": [],
    "cases": []
  },
  "capabilities": { "network": false, "writes": [], "code": false }
}
```

- `id` is the directory name and the install identity. Lowercase, digits, `-`, `.`, `_`.
- `provides.*[].path` must be exactly `files/<kinds>/<name>`, and the folder must exist and be non-empty.
- `requires.hades` is a comma-separated range (`>=0.1.0,<0.2`).
- `requires.tools` names runtime tools the skill expects; it is a declaration, not a grant.
- `capabilities.writes` may name `vault` or `state`. `capabilities.code` must be `false`: code items are not
  a pack in format 1.
- `keywords` are what `hades pack find` searches. Use the words a person would type.

A pack may not declare `roles`, `floor`, `cage` or `policy`. Those are the runtime's, and a download that
could set them would be a download that could disable its own limits.

## Sending one pack to a friend

The archive is the unit. Send the `.hadepack` file and the sha256 beside it; the other side runs:

```
hades pack add ./plan-a-change-1.0.0.hadepack --digest <sha256> --yes
```

Without `--digest` it prints the file's own digest, so the check can be done by eye. No shelf, no clone, no
build.

## Installing

Until `hades pack add` ships, the path is three commands and one decision file:

```bash
curl -LO <this repo>/dist/review-a-diff-1.0.0.hadepack
sha256sum review-a-diff-1.0.0.hadepack          # compare with the row in MENU.md
hades pack inspect review-a-diff-1.0.0.hadepack
hades pack plan review-a-diff-1.0.0.hadepack --state ~/.hades
hades pack apply review-a-diff-1.0.0.hadepack --plan decisions.json --state ~/.hades --activate
```

The sha256 check is the step that matters: the digest in `MENU.md` is what the archive must hash to before
its manifest is worth reading.
