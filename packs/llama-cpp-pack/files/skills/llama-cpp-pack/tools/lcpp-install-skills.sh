#!/usr/bin/env bash
# Install the pack's skills into a Hermes profile, so an agent can load them by name.
#
#   ./lcpp-install-skills.sh                  install into ~/.hermes/skills/mlops
#   ./lcpp-install-skills.sh --dry-run        show what would be copied
#   ./lcpp-install-skills.sh --dest /path     another profile's skills directory
#
# The parent skill points at the pack by absolute path, so the pack has to stay where it is (or
# re-run this after moving it and update PACK_ROOT in the parent skill).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACK_ROOT="$(cd "$HERE/.." && pwd)"
DEST="${HOME}/.hermes/skills/mlops"
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Editing a tool resets its mode, so repair the executable bits on every install.
chmod +x "$PACK_ROOT"/tools/*.py "$PACK_ROOT"/tools/*.sh 2>/dev/null || true

echo "pack:  $PACK_ROOT"
echo "dest:  $DEST"
for skill_dir in "$PACK_ROOT"/skills/*/; do
  name="$(basename "$skill_dir")"
  if [ "$DRY" = "1" ]; then
    echo "  would install $name"
  else
    mkdir -p "$DEST/$name"
    cp "$skill_dir/SKILL.md" "$DEST/$name/SKILL.md"
    echo "  installed $name"
  fi
done

if [ "$DRY" = "0" ]; then
  cat <<EOF

Installed. The skills reference the pack at:
  $PACK_ROOT
If the pack moves, update that path in $DEST/llama-cpp-pack/SKILL.md.
EOF
fi
