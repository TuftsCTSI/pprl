"""Pack everything up into a single entrypoint called with `pprl`"""

# pprl/cli.py
# 2025-11-19
# github.com/mjfos2r

import argparse
import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime as dt

# We set up argparsing in each of these via register_subcommand()
from pprl.commands import create, dedup, match, report, synth, test
COMMAND_MODULES = [
    test,
    report,
    synth,
    create,
    match,
    dedup,
]

curr_dt = dt.strftime(dt.now(), '%H%M%S')

def setup_logging(command, verbose = False):
    """
    Configure global logging.
    While developing this defaults to DEBUG, but once finished will be INFO
    Default level: INFO
    with -v/--verbose: DEBUG
    """
    level = logging.DEBUG if verbose else logging.INFO
    log_dir = Path.cwd() / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{command or 'pprl'}_{curr_dt}.log"
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, mode="w"),
        ],
    )
    logging.getLogger(__name__).debug("Logging to %s", log_file)

def main(argv = None):
    """Define a single entrypoint from which subcommands can be specified"""

    # Main argparser
    parser = argparse.ArgumentParser(
        prog="pprl",
        description="Privacy-preserving record linkage for Tufts CTSI and collaborators"
    )

    # Subcommand argparser
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="command",
        help="Specify which subcommand to execute: [test, create, match, dedup]"
    )

    # Register subcommand modules
    for mod in COMMAND_MODULES:
        mod.register_subcommand(subparsers)

    # Grab the passed args from the CLI call
    args = parser.parse_args(argv)

    # Run the subcommand if provided, else display usage
    if hasattr(args, "func"):
        setup_logging(
            command=getattr(args, "command", None),
            verbose=getattr(args, "verbose", False),
        )
        return args.func(args)
    else:
        parser.print_help()
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
