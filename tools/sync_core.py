#!/usr/bin/env python3
"""Copy core/investkit.py into every skill folder, so each folder runs on its own.

  python tools/sync_core.py          # copy
  python tools/sync_core.py --check  # exit 1 if any copy differs
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core", "investkit.py")
SKILLS = os.path.join(ROOT, "skills")


def main():
    check = "--check" in sys.argv
    with open(CORE, "rb") as f:
        src = f.read()
    bad = 0
    for name in sorted(os.listdir(SKILLS)):
        folder = os.path.join(SKILLS, name)
        if not os.path.isfile(os.path.join(folder, "SKILL.md")) and not any(
                n.endswith(".py") for n in os.listdir(folder)):
            continue
        dest = os.path.join(folder, "investkit.py")
        same = os.path.exists(dest) and open(dest, "rb").read() == src
        if check:
            if not same:
                print("out of date: %s" % dest)
                bad += 1
        elif not same:
            with open(dest, "wb") as f:
                f.write(src)
            print("updated %s" % dest)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
