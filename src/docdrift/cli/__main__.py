"""``python -m docdrift.cli`` - used by the pre-push hook, independent of PATH."""

from docdrift.cli.commands import main

if __name__ == "__main__":
    main()
