#!./venv/bin/python
import argparse
import logging
from pathlib import Path
from pprl import pprl

logger = logging.getLogger(__name__)

def register_subcommand(subparsers):
    """
    register 'synth' as a subcommand within the top level entrypoint.
    """
    # set up synth() command
    synth_parser = subparsers.add_parser(
        "synth",
        help="Generate a synthetic patient identifiers file"
    )
    synth_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging. (DEBUG)"
    )
    synth_parser.add_argument(
        "config", nargs="?", default="./my_files/synth.yml",
        help="Name of the config file for synthesizing data. [Default: synth.yml]"
    )
    synth_parser.set_defaults(func=run_synth)

def run_synth(args):
    """
    CLI handler for synth()
    """
    cfg = Path(args.config)
    logger.info("Starting execution of 'synth' with config: %s", cfg)

    if not cfg.exists():
        logger.error(f"Config file not found: %s", cfg)
        return

    try:
        rc = pprl.synthesize_identifiers(args)
        if rc == 0:
            logger.info("Execution of 'synth' finished successfully.")
        else:
            logger.info("Execution of 'synth' failed with exit code %s", rc)
        return rc
    except Exception:
        logger.exception("Unhandled error during 'synth' execution.")
        return 1
