import os
import sys

if not __package__:
    # Run by path (python3 /path/to/gh_control/__main__.py), e.g. from a menu
    # action whose interpreter does not have gh_control on its sys.path.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from gh_control.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
