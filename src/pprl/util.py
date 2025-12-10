"""Utility functions called by the user-facing PPRL functions"""

import logging
import os
import yaml

import pandas as pd
import csv

from pathlib import Path
from datetime import datetime

UTIL_DIRECTORY = Path(__file__).parent
SOURCE_DIRECTORY = UTIL_DIRECTORY.parent.parent

logger = logging.getLogger(__name__)

def read_config_file(config, allowed_config_names):
    """
    Parse a YAML config file and validate the returned dictionary
    """
    cfg = SOURCE_DIRECTORY / Path(config)
    logger.debug("Parsing config file: %s", config)

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
        raise ValueError
    else:
        unset_config_names = allowed_config_names - observed_config_names
        if bool(unset_config_names):
            logger.warning("The following variables weren't set in the config file:")
            for name in unset_config_names:
                logger.warning(f"    {name}")
            logger.warning("Default values will be assigned instead.")

        return configuration

def validated_file_path(descriptor, file_name, file_directory, file_should_exist = True):
    """
    Assemble, validate, and return a file path
    """
    if file_name is None:
        raise TypeError(f"The name of a {descriptor} file must be provided.")

    file_path = os.path.join(file_directory, file_name)
    file_exists = os.path.isfile(file_path)

    if file_should_exist and not file_exists:
        logger.error("Cannot find %s file: %s", descriptor, file_path)
        raise FileNotFoundError(f'Cannot find {descriptor} file: {file_path}')
        exit(1)
    elif file_exists and not file_should_exist:
        logger.error("The following %s file already exists: %s", descriptor, file_path)
        logger.error("Rather than overwrite this, no output will be written!")
        exit(1)
    else:
        logger.debug("Valid: %s", file_path)
        return file_path

def validated_out_path(descriptor, file_name, file_directory):
    """
    Assemble, validate, and return the path for a new export file
    """
    return validated_file_path(descriptor, file_name, file_directory, file_should_exist = False)

def read_dataframe_from_CSV(file_path):
    logger.debug("Creating DataFrame from: %s", file_path)

    try:
        from collections import defaultdict
        return pd.read_csv(file_path,
                sep = ',',
                #TODO: add tests for non-integer row IDs
                dtype = defaultdict(lambda: str),#, row_id="int",),
                keep_default_na=False,
                )
    except pd.errors.EmptyDataError:
        logger.error("The data file is empty: %s", file_path)
        exit(1)
    #except pd.errors.ParserError:
        #print(f"\nERROR:\n    The data file couldn't be read: {patient_file_path}\n")

def validate_input_fields(df):
    """
    ## Inputs:
    - `df` (pd.DataFrame): DataFrame created from the input_csv containing patient records.

    ## Returns:
    - `df_valid` (pd.DataFrame): DataFrame containing only valid, correctly formatted records, where all fields are present.
    - `df_invalid` (pd.DataFrame): DataFrame containing all invalid records with a column indicating which fields are invalid.

    ## Notes:
        The input DataFrame is copied, then each row has `_clean_row()` applied. which performs validation checks using the following:

        1. `_sanitize_string()`: All strings are capitalized, symbols removed, whitespace stripped.

        2. `_sanitize_date()`: All dates are properly formatted. ("YYYY-MM-DD": `%Y-%m-%d`).

        3. `_sanitize_zip()`: Properly formatted zipcodes. (`#####`, not `#####-####`)

        Rows and their sanitizer functions are defined in the `sanitizer_config` dict and this should be updated to reflect the expected schema.
        Each sanitizer function outputs the cleaned row and a boolean flag for validity.

    ## Config:
        ```
            sanitizer_config = {
                "first": _sanitize_string,
                "last": _sanitize_string,
                "city": _sanitize_string,
                "state": _sanitize_string,
                "zip": _sanitize_zip,
                "dob": _sanitize_date,
        }
        ```
    """
    # define our sanitization functions.
    def _sanitize_string(value):
        """
        Sanitize a string field

        Returns the sanitized value along with a status flag
        """
        raw_str = str(value).strip()

        # Short circuit for any values explicitly marked as invalid
        # (This is currently unused)
        #if not raw_str or raw_str.lower() in ('nan', 'none', '999'):
            #return (value , False)

        # If we've got something here, keep only internal spaces, dashes, apostraphs, and periods, drop all other characters. (if we need to add more, this is trivial)
        clean_value = ''.join(c for c in raw_str if c.isalnum() or c in (' ', '-', "'", '.')).upper()
        # check to see that we've still got something, if length after cleaning is 0 then it's invalid.
        is_valid = True if len(clean_value) > 0 else False
        return clean_value, is_valid

    def _sanitize_zip(value):
        """
        This will sanitize and correctly format zipcodes.
        """
        raw_zip = value.strip() # strip padding
        components = raw_zip.split('-')
        # check for single zip case where it's already valid, is numeric, and is only 5 long.
        if len(components) == 1 and components[0].isnumeric() and len(components[0]) == 5:
            clean_value = raw_zip
            is_valid = True
        # check for zip+4, split by '-' and then check lengths of each component.
        elif len(components) == 2:
            # if both are groovy and both are numeric, pull the first component
            if (len(components[0]) == 5 and len(components[1]) == 4) and (components[0].isnumeric() and components[1].isnumeric()):
                clean_value = components[0]
                is_valid = True
            # if something of the above is not true, then pull the first component anyway but flag as invalid.
            else:
                clean_value = components[0]
                is_valid = False
        # otherwise, something is wrong and this should be flagged. return the original input value and flag as invalid.
        else:
            clean_value = value
            is_valid = False

        return clean_value, is_valid

    def _sanitize_date(value):
        """
        This will sanitize and correctly format dates.
        """
        try:
            date_string = str(value).strip()
            if not date_string or date_string.lower() in ('nan', 'none', 'nat', '999'):
                return (value, False)
            # we're gonna ignore day/month/year formatting because it shouldn't be output that way. if this is an issue
            # then add it to the list below but it shouldn't be stored that way in epic.
            formats = [
                '%Y/%m/%d',
                '%Y-%m-%d',
                '%m-%d-%Y',
                '%m/%d/%Y',
            ]

            for fmt in formats:
                try:
                    clean_date = datetime.strptime(date_string, fmt).strftime('%Y-%m-%d')
                    return (clean_date, True)
                except ValueError:
                    continue

            # Excel dates are annoying and require separate handling.
            # to keep from treating these years as taking place in 2000,
            # compare with current timestamp and knock it back 100 if it's in the future.
            try:
                parsed = datetime.strptime(date_string, '%m/%d/%y')
                if parsed > datetime.now():
                    parsed = parsed.replace(year=parsed.year - 100)
                clean_date = parsed.strftime('%Y-%m-%d')
                return (clean_date, True)

            except ValueError:
                pass

            # If we get nothing. return nothing and flag as invalid.
            return (None, False)

        # if any of the above fails, return nothing and flag as invalid.
        except Exception:
            return (None, False)

    # define which rows get which sanitizing function.
    # This will need to be linked with whatever is expected in the schema
    sanitizer_config = {
        "first": _sanitize_string,
        "last": _sanitize_string,
        "city": _sanitize_string,
        "state": _sanitize_string,
        "zip": _sanitize_zip,
        "dob": _sanitize_date,
    }

    # define the function to use in df.apply()
    def _clean_row(row):
        """
        This takes a row and
            1. sanitizes each field (specified above),
            2. checks for validity
            3. outputs the sanitized row AND a boolean flag under `_is_valid`
            which will be used downstream to split rows into valid/invalid.
        """
        invalid_cols = []
        for col, func in sanitizer_config.items():
            if col not in row.keys():
                continue
            else:
                sanitized, is_valid = func(row[col])
                row[col] = sanitized
                if not is_valid:
                    invalid_cols.append(col)
                row["_valid_row"] = len(invalid_cols) == 0
                row["_invalid_cols"] = invalid_cols
        return row

    # This operates on the DataFrame created by read_dataframe_from_CSV()
    # which already has checks for blank inputs and determines delim automatically.

    raw_df = df.copy()

    # run the sanitizers
    tmp = raw_df.apply(_clean_row, axis=1)

    # copy cleaned and sanitized rows.
    df_valid = tmp[tmp["_valid_row"]].drop(columns=["_valid_row", "_invalid_cols"]).copy()

    # pull invalid rows, then pull the raw data and return a dataframe with the original inputs and the list of invalid cols.
    invalid_mask = ~tmp["_valid_row"]
    invalid_meta = tmp[~tmp["_valid_row"]][["row_id", "_invalid_cols"]].rename(columns={"_invalid_cols": "invalid_cols"})
    df_invalid = raw_df.merge(invalid_meta, on="row_id", how="inner")
    # return the valid dataframe and invalid dataframe.
    return df_valid, df_invalid
