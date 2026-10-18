"""Install the pre-commit secrets check into .git/hooks (Windows and POSIX).

    python scripts\\install_hooks.py
"""
import pathlib
import stat
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".git" / "hooks"

SH = """#!/bin/sh
# installed by scripts/install_hooks.py
python "$(git rev-parse --show-toplevel)/scripts/check_no_secrets.py" --staged
"""


def main() -> int:
    if not HOOKS.parent.exists():
        print("not a git repository yet — run git init first")
        return 1
    HOOKS.mkdir(exist_ok=True)
    hook = HOOKS / "pre-commit"
    hook.write_text(SH, encoding="ascii", newline="\n")
    try:
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    except Exception:
        pass
    print(f"installed {hook}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
