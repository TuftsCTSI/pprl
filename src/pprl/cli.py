"""Pack all scripts into a single interface called with `pprl`"""

# pprl/cli.py
# 2025-11-19
# github.com/mjfos2r

import argparse
import logging
import pytest
import subprocess
import sys

from pathlib import Path
from datetime import datetime as dt

from .pprl import *

logger = logging.getLogger(__name__)

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

def run_standard_function(my_func, args):
    """
    Generic CLI handler for most commands
    """
    cfg = Path(args.config)
    logger.debug("Starting execution of '%s' with config: %s", my_func.__name__, cfg)

    if not cfg.exists():
        logger.error(f"Config file not found: %s", cfg)
        return 1

    # run it and log execution
    try:
        rc = my_func(args)
        if rc == 0:
            logger.debug("Execution of '%s' finished successfully.", my_func.__name__)
        else:
            logger.debug("Execution of '%s' failed with exit code %s", my_func.__name__, rc)
        return rc
    except Exception:
        logger.exception("Unhandled error during '%s' execution.", my_func.__name__)
        return 1

def run_tests(args, as_conformance_report = False):
    """
    CLI handler for running the Pytest suite
    """

    tests_dir = Path(__file__).parent / "tests"
    if not tests_dir.exists():
        logger.error("Tests directory not found at %s", tests_dir)
        return 1

    pytest_args = [str(tests_dir)]

    #TODO: this should probably be handled by pytest_args
    if args.verbose:
        pytest_args.append("-v")

    if as_conformance_report:
        # Disable normal pytest output
        pytest_args.append("-p no:terminal")

        # Pass custom flag for enabling special output
        pytest_args.append("--cmdopt=conformance_report")

    logger.debug("Running pytest using tests at: %s", tests_dir)
    rc = pytest.main(pytest_args)

    if rc == 0:
        logger.debug("Tests passed.")
    else:
        logger.error("Tests failed with exit code %s", rc)

    return rc

def run_create(args):
    """Specific handler for hashing"""
    run_standard_function(create_CLKs, args)

def run_match(args):
    """Specific handler for linking"""
    run_standard_function(match_CLKs, args)

def run_synth(args):
    """Specific handler for creating synthetix data"""
    run_standard_function(synthesize_identifiers, args)

def run_dedup(args):
    """Specific handler for deduplicating a dataset"""
    run_standard_function(deduplicate, args)

def run_conformance_test(args):
    """Handler for generating a conformance report (another flavor of the Pytest suite)"""
    run_tests(args, as_conformance_report = True)

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
        help="Specify which of the following commands to run (e.g., `pprl report`)"
    )

    #TODO: rename create as hash, rename match as link (both breaking changes)
    #TODO: Add tests for the CLI itself?
    # Register subcommand modules
    for command_name, command_description, config_filename, handler_function in [
            (
                "test",
                "Run the project test suite",
                None,
                run_tests,
                ),
            (
                "report",
                "Generate a conformance testing report",
                None,
                run_conformance_test,
                ),
            (
                "create",
                "Hash input data",
                "create_CLKs.yml",
                run_create,
                ),
            (
                "match",
                "Link records based on hashes",
                "match_CLKs.yml",
                run_match,
                ),
            (
                "synth",
                "Generate a synthetic patient identifiers file",
                "synth.yml",
                run_synth,
                ),
            (
                "dedup",
                "Filter out self-linkages from a single patient identifiers input file",
                "dedup.yml",
                run_dedup,
                ),
            ]:
        create_parser = subparsers.add_parser(

            command_name,
            help=command_description
        )
        create_parser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            help="Enable verbose logging. (DEBUG)"
        )
        create_parser.add_argument(
            "config", nargs="?", default = f"./my_files/{config_filename}",
            help="Name of the config file for hash creation. [Default: {config_filename}]"
        )
        create_parser.set_defaults(func=handler_function)

    # Run the subcommand if provided to the CLI all, else display usage
    args = parser.parse_args(argv)
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
