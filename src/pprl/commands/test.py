#!./venv/bin/python

import argparse
import logging
import pytest

logger = logging.getLogger(__name__)

def register_subcommand(subparsers):
    """
    register 'test' as a subcommand within the top level entrypoint.
    """
    # set up create_CLKs() command
    test_parser = subparsers.add_parser(
        "test",
        help="Run the test suite via pytest."
    )
    test_parser.set_defaults(func=run_test)

def run_test(args):
    """
    CLI handler for running tests via pytest
    """
    logger.info("Running pytest.")
    rc = pytest.main([])

    if rc == 0:
        logger.info("Tests passed.")
    else:
        logger.error("Tests failed with exit code %s", rc)

    return rc