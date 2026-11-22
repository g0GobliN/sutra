"""Run the command line interface as ``python -m sutra``.

Hosts that shell out to a plugin (DeepSeek Harness does) cannot rely on a
console script being on their PATH, so the module entry point is the portable
form: ``python -m sutra <command>`` works wherever the package is importable.
"""

import sys

from .cli import main


if __name__ == "__main__":
    sys.exit(main())
