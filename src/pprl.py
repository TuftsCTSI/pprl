import anonlink
import bitarray
import csv
import io
import json
import os
import pandas as pd
import yaml

import clkhash
from clkhash import clk
from clkhash.schema import from_json_dict
from clkhash.serialization import deserialize_bitarray, serialize_bitarray

from anyascii import anyascii
from yaspin import yaspin

def create_CLKs(
    config,
    quiet = False
    ):
    """
    Parse a config file and call the underlying CLK generation
    """

    configuration = yaml.safe_load(open(config))

    _create_CLKs(
        configuration["records"],
        configuration["schema"],
        configuration["secret"],
        configuration["output"],
        quiet = quiet
        )

def _create_CLKs(
    data,
    schema,
    secret,
    output,
    quiet=False
    ):

    schema_file_dir = "schemas"
    user_file_dir = "my_files"


    #TODO: check for file existence, validity, etc.

    with yaspin(text="Reading from files and preprocessing...") as spinner:
        if quiet:
            spinner.stop()
        # Linking schema
        schema_file_name = os.path.join(schema_file_dir, schema)
        with open(schema_file_name, 'r') as f:
            schema_dict = json.load(f)
            schema = from_json_dict(schema_dict)

        # Secret
        secret_file_name = os.path.join(user_file_dir, secret)
        with open(secret_file_name, 'r') as secret_file:
            secret = secret_file.read()

        # Patient identifiers
        patients_file_name = os.path.join(user_file_dir, data)
        patients_df = (
            pd.read_csv(patients_file_name,
                sep=',',
                dtype = str,
                keep_default_na=False,
                nrows = 1e4
            )
            .map(anyascii)
            .map(lambda x: x.upper())
        )

        # Anonlink requires the records CSV and the schema to have the same columns.
        # However, I'd like to test various schemas against the same CSV.
        # So, I do my own error checking and then add empty columns as neededed match the schema.
        features = [{'identifier': x["identifier"], 'ignored': x.get("ignored", False)} for x in schema_dict["features"] if x["identifier"] != ""]
        feature_names = [ x['identifier'] for x in features ]
        unignored_feature_names = [ x['identifier'] for x in features if not x['ignored'] ]
        expected_column_names = ['row_id', 'source', *unignored_feature_names]
        observed_column_names = patients_df.columns.tolist()

        assert observed_column_names == expected_column_names, f" \n\n\
        The data column names or order don't match what is expected from the schema! \n\
            Expected: {','.join(str(i) for i in expected_column_names)} \n\
            Observed: {','.join(str(i) for i in observed_column_names)} \n\
        If you're sure the schema is correct, update your input file to match the schema columns. \n\
        "

        # Add and order missing columns
        for f in feature_names:
            if f not in patients_df.columns:
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
        # Maybe add flags, summary statistics

    hashed_data = clk.generate_clk_from_csv(patients_str, secret, schema, progress_bar = not quiet)

    out_file_name = os.path.join(user_file_dir, output)
    with yaspin(text=f"Writing to {out_file_name}...") as spinner:
        if quiet:
            spinner.stop()
        serialized_hashes = [serialize_bitarray(x) for x in hashed_data]
        patients_df['clk'] = serialized_hashes
        #TODO: optionally print this out for the user to see
        patients_df[['row_id', 'source', 'clk']].to_csv(out_file_name, index=False)

def match_CLKs(
    config,
    quiet=False
    ):
    """
    Parse a config file and call the underlying CLK matching
    """

    configuration = yaml.safe_load(open(config))

    _match_CLKs(
        configuration["input_1"],
        configuration["input_2"],
        configuration["threshold"],
        configuration["output"],
        quiet = quiet
        )

def _match_CLKs(
    input_1,
    input_2,
    threshold,
    output,
    self_match = False,
    quiet = False,
    ):

    user_file_dir = "my_files"
    input_1 = os.path.join(user_file_dir, input_1)
    input_2 = os.path.join(user_file_dir, input_2)
    df_1 = pd.read_csv(input_1)
    df_2 = pd.read_csv(input_2)

    hashed_data_1 = [deserialize_bitarray(x) for x in df_1['clk']]
    hashed_data_2 = [deserialize_bitarray(x) for x in df_2['clk']]

    source_1 = df_1['source'][0]
    source_2 = df_2['source'][0]

    results_candidate_pairs = anonlink.candidate_generation.find_candidate_pairs(
            [hashed_data_1, hashed_data_2],
            anonlink.similarities.dice_coefficient,
            threshold
    )
    solution = anonlink.solving.greedy_solve(results_candidate_pairs)
    found_matches = sorted(list([id_1, id_2] for ((_, id_1), (_, id_2)) in solution))
    if self_match:
        # When linking a dataset against itself, don't link arecord to itself
        #TODO: this should be handled BEFORE solving...
        #TODO: Would we filter for x[0] < x[1]? I assume all mappings are reversible, but that should be a separate test.
        #TODO: already complete? based on self_match, filter out row N matches row N
        # We exclude rows matched to themselves, and we report only unique mappings
        # No (4,4) or both (2,5) and (5,2)
        relevant_matches = [x for x in found_matches if x[0] < x[1]]
    else:
        relevant_matches = found_matches

    #TODO: If sources are 2 collaborating sites, produce a separate output file for each source.
    # That way, each group receives only presence/absence of link
    # This could be toggled in the config file, which I could manually prepare for each site.
    linkages_file_name = os.path.join(user_file_dir, output)
    with open(linkages_file_name, "w") as linkages_file:
        csv_writer = csv.writer(linkages_file)
        csv_writer.writerow([source_1,source_2])
        csv_writer.writerows(relevant_matches)

def self_match_CLKs(
    #TODO: delete this function?
    #TODO: data should be called input, not input_1, and it ought to be positional
    #TODO: consider passing config as separate arguments for this
    config,
    quiet=False
    ):

    configuration = yaml.safe_load(open(config))

    _self_match_CLKs(
        configuration["input"],
        configuration["threshold"],
        configuration["output"],
        quiet = quiet,
        self_match = True
        )
