#!./venv/bin/python

import argparse
import logging
import pytest
from pathlib import Path

logger = logging.getLogger(__name__)

def register_subcommand(subparsers):
    """
    register 'report' as a subcommand within the top level entrypoint.
    """
    # set up report command
    test_parser = subparsers.add_parser(
        "report",
        help="Generate a conformance testing report",
    )
    test_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging. (DEBUG)"
    )
    # add pytest args for better testing
    test_parser.add_argument(
        "--pytest_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments to pass to pytest. Pass them as you would when calling pytest."
    )
    test_parser.set_defaults(func=run_test)

def run_test(args):
    """
    CLI handler for conformance testing report
    """
    # ok so since we want to include tests with the final build?
    # tests have been relocated under src/pprl/tests/

    # let's grab the path now so we can feed it to pytest regardless
    # of where we're executing things.
    tests_dir = Path(__file__).parent.parent / "tests"

    if not tests_dir.exists():
        logger.error("Tests directory not found at %s", tests_dir)
        return 1

    pytest_args = [str(tests_dir)]

    if args.pytest_args:
        pytest_args.extend(args.pytest_args)

    # this should probably be handled by pytest_args but oh well.
    if args.verbose:
        pytest_args.append("-v")

    # Disable normal pytest output for conformance testing report
    pytest_args.append("-p no:terminal")

    # Conformance testing report
    pytest_args.append("--cmdopt=conformance_report")

    logger.debug("Running pytest using tests at: %s", tests_dir)
    rc = pytest.main(pytest_args)

    if rc == 0:
        logger.debug("Tests passed.")
    else:
        logger.error("Tests failed with exit code %s", rc)

    return rc
