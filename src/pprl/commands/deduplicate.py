#!./venv/bin/python
import argparse
import logging
from pathlib import Path
from pprl import pprl

logger = logging.getLogger(__name__)

def register_subcommand(subparsers):
    """
    register 'deduplicate' as a subcommand within the top level entrypoint.
    """
    # set up deduplicate() command
    deduplicate_parser = subparsers.add_parser(
        "deduplicate",
        help="Filter out self-linkages from a patient identifiers input file"
    )
    deduplicate_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging. (DEBUG)"
    )
    deduplicate_parser.add_argument(
        "config", nargs="?", default="deduplicate.yml",
        help="Name of the config file for deduplicating records. [Default: deduplicate.yml]"
    )
    deduplicate_parser.set_defaults(func=run_deduplicate)

def run_deduplicate(args):
    """
    CLI handler for deduplicate()
    """
    cfg = Path(args.config)
    logger.info("Starting execution of 'deduplicate' with config: %s", cfg)

    if not cfg.exists():
        logger.error(f"Config file not found: %s", cfg)
        return

    try:
        rc = pprl.deduplicate(args)
        if rc == 0:
            logger.info("Execution of 'deduplicate' finished successfully.")
        else:
            logger.info("Execution of 'deduplicate' failed with exit code %s", rc)
        return rc
    except Exception:
        logger.exception("Unhandled error during 'deduplicate' execution.")
        return 1
