"""Pack all scripts into a single interface called with `pprl`"""

# pprl/cli.py
# 2025-11-19
# github.com/mjfos2r

import argparse
import logging
import pytest
import subprocess
import sys
import yaml

from pathlib import Path
from datetime import datetime as dt

from .pprl import _create_CLKs, _match_CLKs, _deduplicate, _synthesize_identifiers
#from .util import *

CLI_DIRECTORY = Path(__file__).parent
LOGS_DIRECTORY = CLI_DIRECTORY / "logs"
TESTS_DIRECTORY = CLI_DIRECTORY / "tests"
SOURCE_DIRECTORY = CLI_DIRECTORY.parent.parent
MY_FILES_DIRECTORY = SOURCE_DIRECTORY / "my_files"

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
    log_dir = LOGS_DIRECTORY
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

def read_config_file(config, allowed_config_names):
    """
    Parse a YAML config file and validate the returned dictionary
    """
    cfg = SOURCE_DIRECTORY / Path(config)
    logger.info("Parsing config file: %s", cfg)

    issue_found_yet = False

    configuration = yaml.safe_load(open(cfg))
    if configuration is None:
        issue_found_yet = True
        logger.error("The configuration file contains no discernable options!")

    if not issue_found_yet:
        observed_config_names = set(configuration.keys())
        unexpected_config_names = observed_config_names - allowed_config_names

        if bool(unexpected_config_names):
            issue_found_yet = True
            logger.error("The following variables were not expected in the configuration file:")
            for name in unexpected_config_names:
                logger.error(f"    {name}")

    if issue_found_yet:
        logger.error("Only the following variables should be used:")
        for name in allowed_config_names:
            logger.error(f"    {name}")
        #TODO: add test for this
        #TODO: raise ValueError might make sense, but the stack trace could be unclear to users
        exit(1)
    else:
        unset_config_names = allowed_config_names - observed_config_names
        if bool(unset_config_names):
            logger.warning("The following variables weren't set in the config file:")
            for name in unset_config_names:
                logger.warning(f"    {name}")
            logger.warning("Default values will be assigned instead.")

        return configuration


def parse_args_and_run(my_function, args, permitted_values):
    """
    Generic template to parse a config file and call a subcommand
    """
    
    logger.debug("%s called with %s", __name__, args.config)

    configuration = read_config_file(
            args.config,
            permitted_values
            )
    configuration['verbose'] = args.verbose

    function_name = my_function.__name__
    logger.debug("Calling  %s  with configuration:", function_name)
    for key, value in configuration.items():
        logger.debug("    kwarg: %s = %r", key, value)

    my_function(**configuration)

def run_standard_function(my_func, args):
    """
    Generic CLI handler for most commands
    """

    #TODO: consider rewriting directory logic here and downstream
    #cfg = MY_FILES_DIRECTORY / Path(args.config)
    cfg = SOURCE_DIRECTORY / Path(args.config)
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

    tests_dir = TESTS_DIRECTORY
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
    """User-facing method with config file: hashing"""
    def wrapper_function(args):
        my_function = _create_CLKs
        permitted_values = {'patients', 'schema', 'secret', 'output',}
        parse_args_and_run(my_function, args, permitted_values)
    run_standard_function(wrapper_function, args)

def run_match(args):
    """User-facing method with config file: linking"""
    def wrapper_function(args):
        my_function = _match_CLKs
        permitted_values = {'hashes', 'threshold', 'output',}
        parse_args_and_run(my_function, args, permitted_values)
    run_standard_function(wrapper_function, args)

def run_synth(args):
    def wrapper_function(args):
        """User-facing method with config file: generate synthetic data"""
        my_function = _synthesize_identifiers
        permitted_values = {'n', 'source', 'output', 'seed',}
        parse_args_and_run(my_function, args, permitted_values)
    run_standard_function(wrapper_function, args)

def run_dedup(args):
    """User-facing method with config file: deduplication"""
    def wrapper_function(args):
        my_function = _deduplicate
        permitted_values = {'patients', 'linkages', 'output',}
        parse_args_and_run(my_function, args, permitted_values)
    run_standard_function(wrapper_function, args)

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
                "hash",
                "Hash input data",
                "create_CLKs.yml",
                run_create,
                ),
            (
                "link",
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
