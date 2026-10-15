"""Compatibility entry point for briefing 3.0. See python -m briefing.cli --help."""
from briefing.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
