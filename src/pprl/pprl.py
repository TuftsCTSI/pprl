# pprl.py

### This is required because anonlink is using an outdated version of setuptools
import warnings
warnings.filterwarnings('ignore', message='.*pkg_resources.*')
###

import anonlink
import bitarray
import csv
import io
import json
import logging
import os
import pandas as pd

import clkhash
from clkhash import clk
from clkhash.schema import from_json_dict
from clkhash.serialization import deserialize_bitarray, serialize_bitarray

from anyascii import anyascii
from yaspin import yaspin, Spinner

from faker import Faker
from faker.providers import DynamicProvider

import colorama
from colorama import Fore, Back, Style

from .pprl_utilities import *

logger = logging.getLogger(__name__)

def custom_spinner():
    terms = ".   ", "..  ", "... ", " ...", "  ..", "   .", "    "
    return Spinner(
            ["[" + Style.BRIGHT + Fore.GREEN + x + Style.RESET_ALL + "]" for x in terms],
            200)

def create_CLKs(args):
    """
    Parse a config file and call the underlying CLK generation
    """
    logger.debug("create_CLKs called with %s", args.config)

    configuration = read_config_file(
            args.config,
            {'patients', 'schema', 'secret', 'output', 'data_folder', 'output_folder', 'schema_folder'}
            )
    configuration['verbose'] = args.verbose
    #TODO: check for file format, validity, etc.

    logger.debug("Calling  _create_CLKs  with configuration:")
    for key, value in configuration.items():
        logger.debug("    kwarg: %s = %r", key, value)

    rc = _create_CLKs(**configuration)
    return rc


def _create_CLKs(
        patients = None,
        secret = None,
        schema = 'schema.json',
        output = 'out.csv',
        verbose = False,
        data_folder = os.path.join(os.getcwd(), "my_files"),
        output_folder = os.path.join(os.getcwd(), "my_files"),
        schema_folder = os.path.join(os.getcwd(), "my_files"),
        ):
    logger.debug("Beginning execution within _create_CLKs")

    colorama.init()

    # TODO: possibly break all of this out and put into a separate validate subroutine?

    logger.debug("Validating input filepaths:") # I don't like determining cwd in the arguments.
    patient_file_path = validated_file_path('patient records', patients, data_folder)
    secret_file_path = validated_file_path('secret', secret, data_folder)
    schema_file_path = validated_file_path('schema', schema, schema_folder)

    logger.debug("Validating output filepaths:")
    output_file_path = validated_out_path('hash', output, output_folder)

    output_invalid_records_path = validated_out_path('invalid records', 'invalid_records.csv', output_folder)

    #TODO: Here and throughout, add a separate silent toggle to disable the spinner
    with yaspin(
            custom_spinner(),
            timer = True,
            text=f"Reading from input files",
        ) as spinner:

        logger.debug("Reading schema json into dict.")
        with open(schema_file_path, 'r') as f:
            schema_dict = json.load(f)
            schema = from_json_dict(schema_dict)

        # Secret
        logger.debug("Reading secret from file.")
        with open(secret_file_path, 'r') as secret_file:
            secret = secret_file.read()

        # Create DataFrame from the input csv
        raw_patients_df = read_dataframe_from_CSV(patient_file_path)
        num_records = len(raw_patients_df)

        spinner.ok("[" + Fore.GREEN + "Done" + Style.RESET_ALL + "]")
    logger.info("TOTAL RECORDS: %s", num_records)

    with yaspin(
            custom_spinner(),
            timer = True,
            text=f"Validating records from {patient_file_path}",
        ) as spinner:

        # Pull row_ids and source then drop from dataframe prior to normalizing
        row_ids = raw_patients_df['row_id'].copy()
        source = raw_patients_df['source'].copy()
        data_fields = raw_patients_df.drop(['row_id', 'source'], axis=1)

        # TODO: deal with this separately to catch bad fields/corrupted formatting
        logger.debug("Converting all values to ascii and capitalizing all strings.")
        patients_df = (
                data_fields
                .map(anyascii)
                .map(lambda x: x.upper())
                )
        # add em back
        patients_df.insert(0, 'source', source)
        patients_df.insert(0, 'row_id', row_ids)

        ## Perhaps place this in a breakout subcommand that we can call before execution?
        logger.debug("Validating fields and ensuring standard formatting.")
        patients_df, invalid_records = validate_input_fields(patients_df)

        num_valid_records = len(patients_df)
        num_invalid_records = len(invalid_records)

        spinner.ok("[" + Fore.GREEN + "Done" + Style.RESET_ALL + "]")

    logger.info("VALID RECORDS:   %s", num_valid_records)
    logger.info("INVALID RECORDS: %s", num_invalid_records)

    if num_invalid_records > 0:
        logger.warning("%s INVALID RECORDS DETECTED.", num_invalid_records)
        logger.warning("Writing invalid records to file: %s", output_invalid_records_path)
        with yaspin(
                custom_spinner(),
                timer = True,
                text="Writing invalid records to {output_invalid_records_path}"
            ) as spinner:
            invalid_records.to_csv(output_invalid_records_path, index=False)
        spinner.ok("[" + Fore.GREEN + "Done" + Style.RESET_ALL + "]")
        print("[" + Style.BRIGHT + Fore.YELLOW + "NOTE" + Style.RESET_ALL + "] " + 
                f"Be sure to delete this file when it is no longer needed: {output_invalid_records_path}")

    with yaspin(
            custom_spinner(),
            timer = True,
            text="Generating hashes",
        ) as spinner:


        # Anonlink requires the records CSV and the schema to have the same columns.
        # However, I'd like to test various schemas against the same CSV.
        # So, I do my own error checking and then add empty columns as neededed match the schema.
        # This should also be reordered if the req cols are present but out of order.
        # This can all go into its own helper I think.

        logger.debug("Pulling all present features from the schema.")
        features = [{'identifier': x["identifier"], 'ignored': x.get("ignored", False)} for x in schema_dict["features"] if x["identifier"] != ""]
        feature_names = [ x['identifier'] for x in features ]

        logger.debug("All Features:")
        for name in feature_names:
            logger.debug("    - %s", name)

        ignored_feature_names = [ x['identifier'] for x in features if x['ignored'] ]

        logger.debug("Ignored Features:")
        for name in ignored_feature_names:
            logger.debug("    - %s", name)

        unignored_feature_names = [ x['identifier'] for x in features if not x['ignored'] ]

        logger.debug("UNignored Features:")
        for name in unignored_feature_names:
            logger.debug("    - %s", name)

        expected_column_names = ['row_id', 'source', *unignored_feature_names]
        observed_column_names = patients_df.columns.tolist()

        # in case of mismatch, log what we see and what we want, then exit 1.
        if observed_column_names != expected_column_names:
            logger.error("Names or Order of data columns do not match between schema and input data.")
            expected_col_str = ','.join(str(i) for i in expected_column_names)
            observed_col_str = ','.join(str(i) for i in observed_column_names)
            logger.error("Expected: %s", expected_col_str)
            logger.error("Observed: %s", observed_col_str)
            logger.error("If you're absolutely sure the schema is correct, update your input file to match the schema columns.")
            exit(1)

        # Add and order missing columns

        for f in feature_names:
            if f not in patients_df.columns:
                logger.warning("Adding column %s, and filling with None", f)
                patients_df[f] = None
        patients_df = patients_df[feature_names]

        # Convert df to string (required format for clkhash)
        patients_str = io.StringIO()
        patients_df.to_csv(patients_str)
        patients_str.seek(0)

        # Assert source is identical
        len(set(patients_df['source'])) == 1

        # Assert ID is unique
        len(set(patients_df['row_id'])) == len(patients_df['row_id'])

        #TODO: add date type checking for dob
        # Maybe add flags, summary statistics (what kind of summary stats?)

        logger.debug("Generating clk hashes from input data...")
        hashed_data = clk.generate_clk_from_csv(patients_str, secret, schema, progress_bar = verbose)

        spinner.ok("[" + Fore.GREEN + "Done" + Style.RESET_ALL + "]")

    with yaspin(
            custom_spinner(),
            timer = True,
            text=f"Writing to {output_file_path}",
        ) as spinner:
        logger.debug("Serializing hashes.")
        serialized_hashes = [serialize_bitarray(x) for x in hashed_data]
        patients_df['clk'] = serialized_hashes
        #TODO: optionally print this out for the user to see
        logger.debug("Writing hashes to file: %s", output_file_path)
        patients_df[['row_id', 'source', 'clk']].to_csv(output_file_path, index=False)

        spinner.ok("[" + Fore.GREEN + "Done" + Style.RESET_ALL + "]")

    colorama.init()

    return 0

def match_CLKs(args):
    """
    Parse a config file and call the underlying CLK matching
    """
    logger.debug("match_CLKs called with %s", args.config)

    configuration = read_config_file(
            args.config,
            {'hashes', 'threshold', 'output', 'data_folder', 'output_folder'}
            )

    configuration['verbose'] = args.verbose

    logger.debug("Calling  _match_CLKs  with configuration kwargs:")
    for key, value in configuration.items():
        logger.debug(" %s = %r", key, value)

    rc = _match_CLKs(**configuration)
    return rc

#TODO: change default threshold to 0.975 throughout codebase
def _match_CLKs(
        hashes = None,
        threshold = 0.9,
        output = 'out.csv',
        verbose = False,
        data_folder = 'my_files',
        output_folder = 'my_files',
        ):
    logger.debug("Beginning execution within _match_CLKs")
    #TODO: check other lengths
    if hashes is None:
        raise TypeError('A list of one or two hashes must be provided')
    #TODO: verify it's a list
    if len(hashes) not in {1,2}:
        raise ValueError('A list of one or two hashes must be provided')

    logger.debug("Validating combined output path:")
    linkages_file_path = validated_out_path('linkages', output, output_folder)

    logger.debug("Validating input path: input_1")
    input_1 = validated_file_path('hashes', hashes[0], data_folder)
    if len(hashes) == 2:
        self_match = False
        logger.debug("Validating input path: input_2")
        input_2 = validated_file_path('hashes', hashes[1], data_folder)
    else:
        logger.info("Only one hash detected. Using self_match = True")
        self_match = True
        input_2 = input_1
    #TODO: add checks for self_match
    #TODO: self check should probably be its own method, not automatically inferred


    #TODO: Add some error checks

    logger.debug("Creating dataframes from input csv files.")
    df_1 = read_dataframe_from_CSV(input_1)
    df_2 = read_dataframe_from_CSV(input_2)

    logger.debug("Deserializing bitarrays for both inputs.")
    hashed_data_1 = [deserialize_bitarray(x) for x in df_1['clk']]
    hashed_data_2 = [deserialize_bitarray(x) for x in df_2['clk']]

    source_1 = df_1['source'][0]
    source_2 = df_2['source'][0]
    logger.info("Source 1: %s", source_1)
    logger.info("Source 2: %s", source_2)

    ## TODO: group all of the IO checking into a separate module so we can
    ## pull it out of the args above and also expand this into timestamped
    ## output directories.

    logger.debug("Checking on data presence for source_1 output.")
    s1_output = f"{Path(output).stem}_{source_1}.txt"
    s1_file_path = validated_out_path('linkages', s1_output, output_folder)

    logger.debug("Checking on data presence for source_2 output.")
    s2_output = f"{Path(output).stem}_{source_2}.txt"
    s2_file_path = validated_out_path('linkages', s2_output, output_folder)

    logger.debug("Linking pairs between sources...")
    results_candidate_pairs = anonlink.candidate_generation.find_candidate_pairs(
            [hashed_data_1, hashed_data_2],
            anonlink.similarities.dice_coefficient,
            threshold
            )

    # Rather than finding a single best fit, pull out all potential matches
    #logger.debug("Generating solution...")
    #solution = anonlink.solving.greedy_solve(results_candidate_pairs)
    _, _, (left, right) = results_candidate_pairs
    matching_rows = sorted([(x,y) for x,y in zip(left, right)])
    logger.info("Found %s total matching rows", len(matching_rows))

    if self_match:
        #TODO: filter these out before matching, rather than match then remove?
        # When linking a dataset against itself, don't link a record to itself
        #TODO: Would we filter for x[0] < x[1]? I assume all mappings are reversible, but that should be a separate test.
        #TODO: already complete? based on self_match, filter out row N matches row N
        # We exclude rows matched to themselves, and we report only unique mappings
        # No (4,4) or both (2,5) and (5,2)
        logger.info("Since we matched a dataset against itself, we should ignore matches with the same row number")
        relevant_matches = [x for x in matching_rows if x[0] < x[1]]
        logger.info("There are %s matches between different records", len(relevant_matches))
    else:
        relevant_matches = matching_rows

    row_IDs_of_matches = list([df_1['row_id'][row_n_in_1], df_2['row_id'][row_n_in_2]] for (row_n_in_1, row_n_in_2) in relevant_matches)

    #TODO: consistenly use full file path in all error reporting
    #TODO: why not manually go through user errors with batch testing, capturing output with tee and verifying?
    # That way, each group receives only presence/absence of link
    # This could be toggled in the config file, which I could manually prepare for each site.

    # Okay, so we're gonna add two additional output files.
    # and keep the single unified file for debugging.
    # write the first file.
    logger.info("Writing combined linkages from both sources to file %s", linkages_file_path)
    with open(linkages_file_path, "w") as linkages_file:
        csv_writer = csv.writer(linkages_file)
        csv_writer.writerow([source_1,source_2])
        csv_writer.writerows(row_IDs_of_matches)
    logger.debug("Output successfully written: %s", linkages_file_path)

    if not self_match:
        # now we need to dump matches for source 1
        logger.info("Writing linkages for %s to file %s", source_1, s1_file_path)
        with open(s1_file_path, "w") as linkages_file:
            linkages_file.write(source_1)
            linkages_file.writelines(f"{match[0]}\n" for match in relevant_matches )
        logger.info("Output successfully written: %s", s1_file_path)
        # now we need to dump matches for source 2
        logger.info("Writing linkages for %s to file %s", source_2, s2_file_path)
        with open(s2_file_path, "w") as linkages_file:
            linkages_file.write(source_2)
            linkages_file.writelines(f"{match[1]}\n" for match in relevant_matches )
        logger.info("Output successfully written: %s", s2_file_path)


    return 0

#TODO: Add tests for this
def deduplicate(args):
    """
    Filter out duplicates from a patient identifier file
    """
    logger.debug("deduplicate called with %s", args.config)

    configuration = read_config_file(
            args.config,
            {'patients', 'linkages', 'output', 'data_folder', 'output_folder'}
            )

    configuration['verbose'] = args.verbose

    logger.debug("Calling  _deduplicate with configuration kwargs:")
    for key, value in configuration.items():
        logger.debug(" %s = %r", key, value)

    rc = _deduplicate(**configuration)
    return rc

def _deduplicate(
        patients = None,
        linkages = None,
        output = 'deduplicate.csv',
        verbose = False,
        data_folder = os.path.join(os.getcwd(), "my_files"),
        output_folder = os.path.join(os.getcwd(), "my_files"),
        ):
    logger.debug("Beginning execution within _deduplicate")

    logger.debug("Validating input filepaths:")
    patient_file_path = validated_file_path('patient records', patients, data_folder)
    linkage_file_path = validated_file_path('linkages', linkages, data_folder)

    logger.debug("Validating output filepaths:")
    output_file_path = validated_out_path('output_folder', output, output_folder)

    patients_df = read_dataframe_from_CSV(patient_file_path)
    linkages_df = pd.read_csv(linkage_file_path,
                sep = ',',
                keep_default_na=False,
                )

    source = patients_df['source'].iloc[0]
    duplicate_rows = linkages_df[source].unique()
    filtered_patients_df = patients_df[~patients_df['row_id'].isin(duplicate_rows)]

    logger.info("Writing filtered input to file: %s", output_file_path)
    filtered_patients_df.to_csv(output_file_path, index=False)

    return 0


def read_dataframe_from_CSV(file_path):
    logger.debug("Creating DataFrame from: %s", file_path)

    def get_delimiter(file_path, bytes = 4096):
        # Source - https://stackoverflow.com/a/69796836
        # Posted by pietz
        # Retrieved 2025-11-21, License - CC BY-SA 4.0
        sniffer = csv.Sniffer()
        data = open(file_path, "r").read(bytes)
        delimiter = sniffer.sniff(data).delimiter
        return delimiter

    try:
        from collections import defaultdict
        return pd.read_csv(file_path,
                sep = get_delimiter(file_path),
                dtype = defaultdict(lambda: str, row_id="int",),
                keep_default_na=False,
                )
    except pd.errors.EmptyDataError:
        logger.error("The data file is empty: %s", file_path)
        exit(1)
    #except pd.errors.ParserError:
        #print(f"\nERROR:\n    The data file couldn't be read: {patient_file_path}\n")

def synthesize_identifiers(args):
    """
    Generate a synthetic patient identifier file
    """
    logger.debug("synthesize_identifiers called with %s", args.config)

    configuration = read_config_file(
            args.config,
            {'n', 'source', 'output', 'seed', 'output_folder'}
            )

    configuration['verbose'] = args.verbose

    logger.debug("Calling  _synthesize_identifiers with configuration kwargs:")
    for key, value in configuration.items():
        logger.debug(" %s = %r", key, value)

    rc = _synthesize_identifiers(**configuration)
    return rc

def _synthesize_identifiers(
        n = 100,
        source = None,
        output = 'synthetic_identifiers.csv',
        seed = None,
        verbose = False,
        output_folder = os.path.join(os.getcwd(), "my_files"),
        ):
    logger.debug("Beginning execution within _synthesize_identifiers")

    logger.debug("Validating output filepaths:")
    output_file_path = validated_out_path('output_folder', output, output_folder)

    #TODO: if it ever becomes a priority, set up custom frequencies for each field
    #TODO: consider using a more sophisticated tool for reaslistic data

    # We'll limit states to our geographical region.
    # Note that ZIP and City are ficitonal and independent.
    custom_state_provider = DynamicProvider(
         provider_name="custom_state",
         elements=["CT", "MA", "RI", "NH"],
    )

    fake = Faker()
    fake.add_provider(custom_state_provider)
    Faker.seed(2026)

    logger.info("Writing output to file: %s", output_file_path)

    with open(output_file_path, mode = 'w') as file:
        writer = csv.writer(file)

        writer.writerow(['row_id', 'source', 'first', 'last', 'city', 'state', 'zip', 'dob'])
        for i in range(1,n+1):
            writer.writerow([
                i, # These can be customized
                source, # This is a constant
                fake.first_name(),
                fake.last_name(),
                # Note that city, state, and ZIP are independent (addresses aren't coherent)
                fake.city(),
                fake.custom_state(),
                fake.zipcode(),
                fake.date_of_birth().strftime("%Y-%m-%d")
                ])

    return 0
