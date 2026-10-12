"""Allow ``python -m docdrift`` (used by the pre-push hook, independent of PATH)."""

from docdrift.cli import main

if __name__ == "__main__":
    main()
