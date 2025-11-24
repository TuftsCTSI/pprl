#!./venv/bin/python

import argparse
import logging
from pathlib import Path
from pprl import pprl

logger = logging.getLogger(__name__)

def register_subcommand(subparsers):
    """
    register 'create' as a subcommand within the top level entrypoint.
    """
    # set up create_CLKs() command
    create_parser = subparsers.add_parser(
        "create",
        help="Create hashes for the input data specified in create_CLKs.yml"
    )
    create_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging. (DEBUG)"
    )
    create_parser.add_argument(
        "config", nargs="?", default = "./my_files/create_CLKs.yml",
        help="Name of the config file for hash creation. [Default: create_CLKs.yml]"
    )
    create_parser.set_defaults(func=run_create)

def run_create(args):
    """
    CLI handler for create_CLKs()
    """
    cfg = Path(args.config)
    logger.info("Starting execution of 'create_CLKs' with config: %s", cfg)

    if not cfg.exists():
        logger.error(f"Config file not found: %s", cfg)
        return 1

    # run it and log execution
    try:
        rc = pprl.create_CLKs(args)
        if rc == 0:
            logger.info("Execution of 'create_CLKs' finished successfully.")
        else:
            logger.info("Execution of 'create_CLKs' failed with exit code %s", rc)
        return rc
    except Exception:
        logger.exception("Unhandled error during 'create_CLKs' execution.")
        return 1
