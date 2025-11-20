import logging
import os
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

def read_config_file(config, allowed_config_names):
    """
    Parse a YAML config file and validate the returned dictionary
    """
    logger.info("Parsing config file: %s", config)

    configuration = yaml.safe_load(open(config))
    observed_config_names = set(configuration.keys())
    unexpected_config_names = observed_config_names - allowed_config_names

    if bool(unexpected_config_names):
        logger.error("The following variables were not expected in the configuration file:")
        for name in unexpected_config_names:
            logger.error(f"    unexpected: {name}")
        logger.error("Only the following variables should be used:")
        for name in allowed_config_names:
            logger.error(f"    allowed: {name}")
        exit(1)
        #raise ValueError

    #TODO: test to avoid mixing hashing schema with linking schema?

    unused_config_names = allowed_config_names - observed_config_names
    if bool(unexpected_config_names):
        logger.warning("The following variables weren't set in the config file:")
        for name in unused_config_names:
            logger.warning("unused: %s", name)
        logger.warning("Default values will be assigned instead.")

    #configuration.setdefault('schema', 'schema.json')
    #configuration.setdefault('secret', 'secret.txt')
    #configuration.setdefault('output', 'out.csv')
    #configuration.setdefault('quiet', True)
    #configuration.setdefault('data_folder', os.path.join(os.getcwd(), "my_files"))
    #configuration.setdefault('schema_folder', os.path.join(os.getcwd(), "schemas"))

#TODO: warn a user if any unexpected names appear in the dictionary!
#TODO: warn a user if a default value is used
#TODO: to be really nice, include a list of all potential errors, and only then exist

    return configuration

def validated_file_path(descriptor, file_name, file_directory):
    """
    Assmeble, validate, and return a file path
    """
    if file_name is None:
        raise TypeError(f"The name of a {descriptor} file must be provided.")
    file_path = os.path.join(file_directory, file_name)
    if not os.path.isfile(file_path):
        logger.error("Cannot find %s file: %s", descriptor, file_path)
        exit(1)
        #raise FileNotFoundError(f'Cannot find {descriptor} file: {file_path}')
    logger.debug("Valid: %s", file_path)
    return file_path

def validated_out_path(descriptor, file_name, file_directory):
    """
    Assmeble, validate, and return the path for a new export file
    """
    if file_name is None:
        raise TypeError(f"The name of a {descriptor} file must be provided.")
    file_path = os.path.join(file_directory, file_name)
    if os.path.isfile(file_path):
        logger.error("The following %s file already exists: %s", descriptor, file_path)
        logger.error("Rather than overwrite this, no output will be written!")
        exit(1)
        #raise FileNotFoundError(f'The following {descriptor} file already exists: {file_path}')
    logger.debug("Valid: %s", file_path)
    return file_path

