# pprl/cli.py
# 2025-11-19
# github.com/mjfos2r

import argparse
import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime as dt

from pprl.commands import create, match, test, filter

curr_dt = dt.strftime(dt.now(), '%H%M%S')


# we setup argparsing in each of these via register_subcommand()
COMMAND_MODULES = [
    create,
    filter,
    match,
    test,
]

def setup_logging(command, verbose = False):
    """
    Configure global logging.
    While developing this defauls to DEBUG, but once finished will be INFO
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
# So this script exists to pack everything up into a single entrypoint called with pprl

def main(argv = None):
    """Define a single entrypoint from which subcommands can be specified.
        pprl -> return usage.
        pprl test -> execute pytest.
        pprl create -> run create_CLKs()
        pprl match -> run match_CLKs()
        pprl filter -> Filter the patient identifier file for self-linkages

        global args:
            -v/--verbose: set logging level to DEBUG
    """

    # set up main argparser
    parser = argparse.ArgumentParser(
        prog="pprl",
        description="Privacy-preserving record linkage for Tufts CTSI and collaborators"
    )

    # set up subcommand argparser
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="command",
        help="Specify which subcommand to execute: [test, create, match, filter]"
    )

    # have each command module register itself
    for mod in COMMAND_MODULES:
        mod.register_subcommand(subparsers)

    # grab the passed args from the cli call
    args = parser.parse_args(argv)

    # no subcommand, display usage.
    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    # initialize logging after argparsing
    setup_logging(
        command=getattr(args, "command", None),
        verbose=getattr(args, "verbose", False),
    )

    # otherwise
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())

