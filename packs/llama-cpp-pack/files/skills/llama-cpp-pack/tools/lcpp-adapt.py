#!/usr/bin/env python3
"""Adapt the llama.cpp serving pack to this machine.

Thin wrapper: the engine is tools/pack-adapt.py, the pack-specific checks are tools/lcpp-checks.py,
and the assumptions live in pack.manifest.json. Keeping them separate means another pack can reuse
the engine by writing its own manifest and checker, which is the point.

    ./lcpp-adapt.py             check every assumption, write ~/.config/pack-adapt/llama-cpp-pack.profile.json
    ./lcpp-adapt.py --dry-run   report only
    ./lcpp-adapt.py --show      the profile from last time
"""
import runpy
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().with_name("pack-adapt.py")
sys.argv = [str(ENGINE)] + sys.argv[1:]
runpy.run_path(str(ENGINE), run_name="__main__")
