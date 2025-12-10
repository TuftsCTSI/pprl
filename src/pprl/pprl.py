# pprl.py

### This is required because anonlink is using an outdated version of setuptools
import warnings
warnings.filterwarnings('ignore', message='.*pkg_resources.*')
###

import anonlink
import bitarray
import io
import json
import logging
import os

import clkhash
from clkhash import clk
from clkhash.schema import from_json_dict
from clkhash.serialization import deserialize_bitarray, serialize_bitarray

from anyascii import anyascii
from yaspin import yaspin, Spinner

import colorama
from colorama import Fore, Back, Style

from .util import *

from pathlib import Path

PPRL_DIRECTORY = Path(__file__).parent
SOURCE_DIRECTORY = PPRL_DIRECTORY.parent.parent
MY_FILES_DIRECTORY = SOURCE_DIRECTORY / "my_files"

logger = logging.getLogger(__name__)

def spin_on(text):
    """Display a custom spinner that matches the format of our info messages"""

    terms = ".   ", "..  ", "... ", " ...", "  ..", "   .", "    "
    formatted_terms = ["[" + Style.BRIGHT + Fore.GREEN + x + Style.RESET_ALL + "]" for x in terms]

    frame_duration = 200

    return yaspin(
        Spinner(formatted_terms, frame_duration),
        timer = True,
        text=text,
    )

def spin_off(spinner):
    spinner.ok("[" + Fore.GREEN + "Done" + Style.RESET_ALL + "]")

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

def create_CLKs(args):
    """User-facing method with config file: hashing"""
    my_function = _create_CLKs
    permitted_values = {'patients', 'schema', 'secret', 'output',}
    parse_args_and_run(my_function, args, permitted_values)

def match_CLKs(args):
    """User-facing method with config file: linking"""
    my_function = _match_CLKs
    permitted_values = {'hashes', 'threshold', 'output',}
    parse_args_and_run(my_function, args, permitted_values)

def deduplicate(args):
    """User-facing method with config file: deduplication"""
    my_function = _deduplicate
    permitted_values = {'patients', 'linkages', 'output',}
    parse_args_and_run(my_function, args, permitted_values)

def synthesize_identifiers(args):
    """User-facing method with config file: generate synthetic data"""
    my_function = _synthesize_identifiers
    permitted_values = {'n', 'source', 'output', 'seed',}
    parse_args_and_run(my_function, args, permitted_values)

def _create_CLKs(
        patients = None,
        secret = None,
        schema = 'schema.json',
        output = 'out.csv',
        verbose = False,
        data_folder = MY_FILES_DIRECTORY,
        output_folder = MY_FILES_DIRECTORY,
        schema_folder = MY_FILES_DIRECTORY,
        ):
    """Internal method for hashing"""
    logger.debug("Beginning execution within _create_CLKs")

    colorama.init()

    # TODO: possibly break all of this out and put into a separate validate subroutine?

    logger.debug("Validating file paths")
    patient_file_path = validated_file_path('patient records', patients, data_folder)
    secret_file_path = validated_file_path('secret', secret, data_folder)
    schema_file_path = validated_file_path('schema', schema, schema_folder)
    output_file_path = validated_out_path('hash', output, output_folder)
    output_invalid_records_path = validated_out_path('invalid records', 'invalid_records.csv', output_folder)

    #TODO: Here and throughout, add a separate silent toggle to disable the spinner
    #TODO: This would be most useful for automated tests of the CLI itself
    with spin_on(f"Reading from input files") as spinner:

        logger.debug("Parsing schema JSON into dict.")
        with open(schema_file_path, 'r') as f:
            schema_dict = json.load(f)
            schema = from_json_dict(schema_dict)

        logger.debug("Reading secret from file.")
        with open(secret_file_path, 'r') as secret_file:
            secret = secret_file.read()
        if secret == "":
            raise ValueError(f'The secret file cannot be empty: {secret_file_path}')

        logger.debug("Reading identifiers from file.")
        raw_patients_df = read_dataframe_from_CSV(patient_file_path)
        num_records = len(raw_patients_df)

        spin_off(spinner)

    logger.info("TOTAL RECORDS: %s", num_records)

    with spin_on(f"Validating records from {patient_file_path}") as spinner:

        #TODO: Add test set for each
        logger.debug("Removing special columns (row_id and source)")
        row_ids = raw_patients_df['row_id'].copy()
        source = raw_patients_df['source'].copy()
        data_fields = raw_patients_df.drop(['row_id', 'source'], axis=1)

        all_source_values_identical = len(set(source)) == 1
        assert all_source_values_identical

        all_row_ID_values_unique = len(set(row_ids)) == len(row_ids)
        assert all_row_ID_values_unique

        # TODO: deal with this separately to catch bad fields/corrupted formatting
        logger.debug("Normalizing all remaining (data) values")
        patients_df = (
                data_fields
                .map(anyascii)
                .map(lambda x: x.upper())
                )

        logger.debug("Adding back (unnormalized) special columns")
        patients_df.insert(0, 'source', source)
        patients_df.insert(0, 'row_id', row_ids)

        ## Perhaps place this in a breakout subcommand that we can call before execution?
        logger.debug("Validating fields and ensuring standard formatting.")
        patients_df, invalid_records = validate_input_fields(patients_df)

        num_valid_records = len(patients_df)
        num_invalid_records = len(invalid_records)

        spin_off(spinner)

    logger.info("VALID RECORDS:   %s", num_valid_records)
    logger.info("INVALID RECORDS: %s", num_invalid_records)

    if num_invalid_records > 0:
        logger.warning("%s INVALID RECORDS DETECTED.", num_invalid_records)
        logger.warning("Writing invalid records to file: %s", output_invalid_records_path)
        with spin_on(f"Writing invalid records to {output_invalid_records_path}") as spinner:
            invalid_records.to_csv(output_invalid_records_path, index=False)
            spin_off(spinner)
        print("[" + Style.BRIGHT + Fore.YELLOW + "NOTE" + Style.RESET_ALL + "] " + 
                f"Be sure to delete this file when it is no longer needed: {output_invalid_records_path}")

    with spin_on(f"Checking data against schema") as spinner:

        # Anonlink requires the records CSV and the schema to have the same columns.
        # However, I'd like to test various schemas against the same CSV.
        # So, I do my own error checking and then add empty columns as neededed match the schema.
        # This should also be reordered if the req cols are present but out of order.
        # This can all go into its own helper I think.

        logger.debug("Pulling all present features from the schema.")
        features = [
                {'identifier': x["identifier"], 'ignored': x.get("ignored", False)}
                for x in schema_dict["features"] if x["identifier"] != ""
                ]

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

        #TODO: add date type checking for dob

        spin_off(spinner)

    logger.debug("Generating clk hashes from input data...")
    #TODO: progress_bar toggle should take a value from (currently unimplemented) spinner toggle
    hashed_data = clk.generate_clk_from_csv(patients_str, secret, schema, progress_bar = True)

    with spin_on(f"Writing to {output_file_path}") as spinner:
        logger.debug("Serializing hashes.")
        serialized_hashes = [serialize_bitarray(x) for x in hashed_data]
        patients_df['clk'] = serialized_hashes
        #TODO: optionally print this out for the user to see
        logger.debug("Writing hashes to file: %s", output_file_path)
        patients_df[['row_id', 'source', 'clk']].to_csv(output_file_path, index=False)

        spin_off(spinner)

    colorama.deinit()

    return 0

#TODO: source is included in the filename of the output, so format should be strictly enforced
def _match_CLKs(
        hashes = None,
        threshold = 0.9,
        output = 'out.csv',
        verbose = False,
        data_folder = MY_FILES_DIRECTORY,
        output_folder = MY_FILES_DIRECTORY,
        ):
    """Internal method for linking"""
    logger.debug("Beginning execution within _match_CLKs")

    colorama.init()

    logger.debug("Validating list of hash files")
    if hashes is None:
        raise TypeError('A list of one or two hashes must be provided')
    #TODO: Add corresponding test
    assert isinstance(hashes, list)
    if len(hashes) not in {1,2}:
        raise ValueError('A list of one or two hashes must be provided')

    logger.debug("Validating file paths")
    linkages_file_path = validated_out_path('linkages', output, output_folder)
    input_1 = validated_file_path('hashes', hashes[0], data_folder)
    if len(hashes) == 2:
        logger.info("Two hash files were provided. Using self_match = False")
        self_match = False
        input_2 = validated_file_path('hashes', hashes[1], data_folder)
    else:
        logger.info("Only one hash file was provided. Using self_match = True")
        self_match = True
        input_2 = input_1
    #TODO: add checks for self_match
    #TODO: self check should probably be its own method, not automatically inferred

    #TODO: Add some error checks

    logger.debug("Creating dataframes from input csv files.")
    df_1 = read_dataframe_from_CSV(input_1)
    df_2 = read_dataframe_from_CSV(input_2)

    #TODO: should we assert matching source within file, as we do when hashing?
    source_1 = df_1['source'][0]
    source_2 = df_2['source'][0]
    #TODO: update logging info for case of self_match == True
    logger.debug("Checking on data presence for source_1 output.")
    #s1_output = f"{Path(output).stem}_{source_1}.txt"
    s1_output = f"{source_1}_duplicates.csv"
    s1_file_path = validated_out_path('duplicates', s1_output, output_folder)
    if self_match:
        logger.info("Source: %s", source_1)
    else:
        logger.info("Source 1: %s", source_1)
        logger.info("Source 2: %s", source_2)
        #if source_1 == source_2:
            #TODO: Add a test for this and throw an error
        logger.debug("Checking on data presence for source_2 output.")
        #s2_output = f"{Path(output).stem}_{source_2}.txt"
        s2_output = f"{source_2}_duplicates.csv"
        s2_file_path = validated_out_path('duplicates', s2_output, output_folder)

    ## TODO: group all of the IO checking into a separate module so we can
    ## pull it out of the args above and also expand this into timestamped
    ## output directories.

    #TODO: any way to force spinner to update (better yet, show progress!)
    with spin_on(f"Calculating probabilities (Timer freezing is normal)") as spinner:

        logger.debug("Deserializing bitarrays for both inputs.")
        hashed_data_1 = [deserialize_bitarray(x) for x in df_1['clk']]
        hashed_data_2 = [deserialize_bitarray(x) for x in df_2['clk']]

        logger.debug("Linking pairs between sources...")
        results_candidate_pairs = anonlink.candidate_generation.find_candidate_pairs(
                [hashed_data_1, hashed_data_2],
                anonlink.similarities.dice_coefficient,
                threshold
                )

        # We diverge from the clkhash tutorial here.
        # Rather than finding a single best fit, we pull out all potential matches.
        # The following could be used if a single best fit is needed:
        #logger.debug("Generating solution...")
        #solution = anonlink.solving.greedy_solve(results_candidate_pairs)

        _, _, (left, right) = results_candidate_pairs
        matching_rows = sorted([(x,y) for x,y in zip(left, right)])

        if self_match:
            #TODO: avoid calculating unneeded probabilities (see discussion below)

            # Our current implementation links each hash from file 1 with each hasj of file 2.
            # This makes perfect sense when file 1 and file 2 are different.

            # However, in this branch (self_match == True), we are comparing a file against itself.
            # That means we don't need to consider if row N from file 1 is linked to row N from file 2.
            # After all, they're the same record!
            # More importantly, assuming that linkages are commutative, we can ignore half of the remaining linkages.
            # That is, if we compare row A against row B, we don't need to compare row B against row A.
            # For N and M hashes (N<M), we always calculate N*M probabilities, but I think we only need N*M/2 - N.

            # The issue is that I don't know any way to adapt the tooling to take advantage of this
            # The relevant function is anonlink.candidate_generation.find_candidate_pairs
            # The current approach generates all these linkage probabilities, of which we discard more than half.

            #TODO: add test set to indicate if matches are commutative
            #TODO: add other tests in general to determine if order affects the probability

            logger.debug("Since we matched a dataset against itself, we should ignore matches with the same row number")
            relevant_matches = [x for x in matching_rows if x[0] < x[1]]
            logger.debug("There are %s matches between distinct records", len(relevant_matches))
        else:
            relevant_matches = matching_rows

        spin_off(spinner)

    logger.info("Found %s total matching rows", len(relevant_matches))

    #TODO: For CLI tests, manually go through user errors with batch testing, capturing output with tee and verifying?

    with spin_on(f"Writing linkage pairs from both sources to {linkages_file_path}") as spinner:

        row_IDs_of_matches = list([df_1['row_id'][row_n_in_1], df_2['row_id'][row_n_in_2]] for (row_n_in_1, row_n_in_2) in relevant_matches)

        logger.debug("Writing combined linkages from both sources to file %s", linkages_file_path)
        with open(linkages_file_path, "w") as linkages_file:
            csv_writer = csv.writer(linkages_file)
            csv_writer.writerow([source_1,source_2])
            csv_writer.writerows(row_IDs_of_matches)
        logger.debug("Output successfully written: %s", linkages_file_path)

        spin_off(spinner)

    #TODO: Add CI test that ensures these two lists haven't been swapped?
    #TODO: Add test on these files, as they can be significant outputs depending on study design
    if not self_match:
        with spin_on(f"Writing {source_1} duplicates to {s1_file_path}") as spinner:
            logger.debug("Writing linkages for %s to file %s", source_1, s1_file_path)
            with open(s1_file_path, "w") as linkages_file:
                csv_writer = csv.writer(linkages_file)
                csv_writer.writerow([source_1])
                csv_writer.writerows(sorted([[i] for i,_ in row_IDs_of_matches]))
            logger.debug("Output successfully written: %s", s1_file_path)
            spin_off(spinner)
        with spin_on(f"Writing {source_2} duplicates to {s2_file_path}") as spinner:
            logger.debug("Writing linkages for %s to file %s", source_2, s2_file_path)
            with open(s2_file_path, "w") as linkages_file:
                csv_writer = csv.writer(linkages_file)
                csv_writer.writerow([source_2])
                csv_writer.writerows(sorted([[j] for _,j in row_IDs_of_matches]))
            logger.debug("Output successfully written: %s", s2_file_path)
            spin_off(spinner)

    colorama.deinit()

    return 0

#TODO: Add tests for this
def _deduplicate(
        patients = None,
        linkages = None,
        output = 'deduplicate.csv',
        verbose = False,
        data_folder = MY_FILES_DIRECTORY,
        output_folder = MY_FILES_DIRECTORY,
        ):
    """Internal method for deduplication"""
    logger.debug("Beginning execution within _deduplicate")

    colorama.init()

    logger.debug("Validating file paths")
    patient_file_path = validated_file_path('patient records', patients, data_folder)
    linkage_file_path = validated_file_path('linkages', linkages, data_folder)
    output_file_path = validated_out_path('output_folder', output, output_folder)

    patients_df = read_dataframe_from_CSV(patient_file_path)
    linkages_df = read_dataframe_from_CSV(linkage_file_path)

    source = patients_df['source'].iloc[0]
    duplicate_rows = linkages_df[source].unique()
    filtered_patients_df = patients_df[~patients_df['row_id'].isin(duplicate_rows)]

    logger.debug("Writing filtered input to file: %s", output_file_path)
    filtered_patients_df.to_csv(output_file_path, index=False)

    colorama.deinit()

    return 0

def _synthesize_identifiers(
        n = 100,
        source = None,
        output = 'synthetic_identifiers.csv',
        seed = None,
        verbose = False,
        output_folder = MY_FILES_DIRECTORY,
        ):
    """Internal method for generating synthetic data"""
    logger.debug("Beginning execution within _synthesize_identifiers")

    colorama.init()

    logger.debug("Validating file paths")
    output_file_path = validated_out_path('output_folder', output, output_folder)

    with spin_on(f"Writing synthetic identifiers to {output_file_path}") as spinner:

        from faker import Faker
        from faker.providers import DynamicProvider

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

        logger.debug("Writing output to file: %s", output_file_path)

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

        spin_off(spinner)

    colorama.deinit()

    return 0
