#!./venv/bin/python
import argparse
import logging
from pathlib import Path
from pprl import pprl

logger = logging.getLogger(__name__)

def register_subcommand(subparsers):
    """
    register 'match' as a subcommand within the top level entrypoint.
    """
    # set up match_CLKs() command
    match_parser = subparsers.add_parser(
        "match",
        help="Identify matched records between two files containing hashed records."
    )
    match_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging. (DEBUG)"
    )
    match_parser.add_argument(
        "config", nargs="?", default="match_CLKs.yml",
        help="Name of the config file for matching records. [Default: match_CLKs.yml]"
    )
    match_parser.set_defaults(func=run_match)

def run_match(args):
    """
    CLI handler for match_CLKs()
    """
    cfg = Path(args.config)
    logger.info("Starting execution of 'match_CLKs' with config: %s", cfg)

    if not cfg.exists():
        logger.error(f"Config file not found: %s", cfg)
        return

    try:
        rc = pprl.match_CLKs(args)
        if rc == 0:
            logger.info("Execution of 'match_CLKs' finished successfully.")
        else:
            logger.info("Execution of 'match_CLKs' failed with exit code %s", rc)
        return rc
    except Exception:
        logger.exception("Unhandled error during 'match_CLKs' execution.")
        return 1