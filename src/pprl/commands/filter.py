#!./venv/bin/python
import argparse
import logging
from pathlib import Path
from pprl import pprl

logger = logging.getLogger(__name__)

def register_subcommand(subparsers):
    """
    register 'filter' as a subcommand within the top level entrypoint.
    """
    # set up filter_by_linkages() command
    filter_parser = subparsers.add_parser(
        "filter",
        help="Filter out self-linkages from a patient identifiers input file"
    )
    filter_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging. (DEBUG)"
    )
    filter_parser.add_argument(
        "config", nargs="?", default="filter.yml",
        help="Name of the config file for filtering records. [Default: filter.yml]"
    )
    filter_parser.set_defaults(func=run_filter)

def run_filter(args):
    """
    CLI handler for filter_by_linkages()
    """
    cfg = Path(args.config)
    logger.info("Starting execution of 'filter_by_linkages' with config: %s", cfg)

    if not cfg.exists():
        logger.error(f"Config file not found: %s", cfg)
        return

    try:
        rc = pprl.filter_by_linkages(args)
        if rc == 0:
            logger.info("Execution of 'filter_by_linkages' finished successfully.")
        else:
            logger.info("Execution of 'filter_by_linkages' failed with exit code %s", rc)
        return rc
    except Exception:
        logger.exception("Unhandled error during 'filter_by_linkages' execution.")
        return 1
