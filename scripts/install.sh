#!/usr/bin/env bash
# SUTRA universal installer for Linux and macOS.
#
# Creates an isolated virtual environment, installs the `sutra-agent`
# distribution into it, and verifies the CLI. Safe to re-run: an existing
# environment is reused and the package is upgraded in place.
#
#   curl -fsSL https://raw.githubusercontent.com/g0GobliN/sutra/main/scripts/install.sh | bash
#   ./scripts/install.sh --dry-run
#
# Environment overrides:
#   SUTRA_VENV     virtual environment location (default: ~/.sutra-venv)
#   SUTRA_PACKAGE  distribution to install (default: sutra-agent)
#   SUTRA_PYTHON   interpreter to use (default: newest python >= 3.11 found)
set -euo pipefail

VENV="${SUTRA_VENV:-$HOME/.sutra-venv}"
PACKAGE="${SUTRA_PACKAGE:-sutra-agent}"
DRY_RUN=0

usage() {
    cat <<EOF
SUTRA installer

usage: install.sh [--dry-run] [--venv PATH] [--help]

  --dry-run   print the actions without writing anything
  --venv PATH install into PATH (default: \$SUTRA_VENV or ~/.sutra-venv)
  --help      show this message
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run|-n) DRY_RUN=1; shift ;;
        --venv)
            if [ "$#" -lt 2 ]; then
                echo "sutra: --venv needs a path" >&2
                exit 2
            fi
            VENV="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *)
            echo "sutra: unknown option $1" >&2
            usage >&2
            exit 2 ;;
    esac
done

supports_python() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        >/dev/null 2>&1
}

find_python() {
    if [ -n "${SUTRA_PYTHON:-}" ]; then
        if supports_python "$SUTRA_PYTHON"; then
            printf '%s\n' "$SUTRA_PYTHON"
            return 0
        fi
        echo "sutra: SUTRA_PYTHON=$SUTRA_PYTHON is not Python 3.11 or newer" >&2
        return 1
    fi
    for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && supports_python "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

if ! PYTHON="$(find_python)"; then
    cat >&2 <<'EOF'
sutra: Python 3.11 or newer is required and none was found.

Install Python first, then re-run this script:
  - Debian/Ubuntu:  sudo apt install python3 python3-venv
  - Fedora:         sudo dnf install python3
  - macOS:          brew install python
EOF
    exit 1
fi

echo "sutra: using $("$PYTHON" --version 2>&1) at $PYTHON"
echo "sutra: virtual environment: $VENV"

if [ "$DRY_RUN" -eq 1 ]; then
    if [ -d "$VENV" ]; then
        echo "sutra: would reuse the existing environment"
    else
        echo "sutra: would create the environment"
    fi
    echo "sutra: would install or upgrade $PACKAGE"
    echo "sutra: would verify $VENV/bin/sutra --version"
    echo "sutra: dry run; nothing was written"
    exit 0
fi

if [ -d "$VENV" ]; then
    echo "sutra: reusing the existing environment"
else
    "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade "$PACKAGE"
"$VENV/bin/sutra" --version

cat <<EOF

sutra: installed.

Add it to this shell:
  export PATH="$VENV/bin:\$PATH"

Then wire it into the agents you have installed:
  sutra install

Verify the integrations with:
  sutra doctor
EOF
