import anonlink
import bitarray
import csv
import io
import json
#import logging
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
    ):
    """
    Parse a config file and call the underlying CLK generation
    """

    configuration = read_config_file(
            config,
            {'patients', 'schema', 'secret', 'output', 'quiet', 'data_folder', 'output_folder', 'schema_folder'}
            )

#    print(f"""
#        The following files will be read from {data_folder}:
#          - {data}
#          - {secret}
#        The following files will be written to {data_folder}:
#          - {output}
#        The following schema will be read from {schema_folder}:
#          - {schema}
#        The following options will be applied:
#          - quiet = {quiet}
#        """)

    _create_CLKs(**configuration)

def _create_CLKs(
    patients = None,
    secret = None,
    schema = 'schema.json',
    output = 'out.csv',
    quiet = False,
    data_folder = os.path.join(os.getcwd(), "my_files"),
    output_folder = os.path.join(os.getcwd(), "my_files"),
    schema_folder = os.path.join(os.getcwd(), "schemas"),
    ):
    #TODO: check for file existence, validity, etc.

    with yaspin(text="Reading from files and preprocessing...") as spinner:
        if quiet:
            spinner.stop()
        # Linking schema
        schema_file_name = os.path.join(schema_folder, schema)
        with open(schema_file_name, 'r') as f:
            schema_dict = json.load(f)
            schema = from_json_dict(schema_dict)

        # Secret
        secret_file_name = os.path.join(data_folder, secret)
        with open(secret_file_name, 'r') as secret_file:
            secret = secret_file.read()

        # Patient identifiers
        patients_file_name = os.path.join(data_folder, patients)
        raw_patients_df = pd.read_csv(patients_file_name,
                sep=',',
                dtype = str,
                keep_default_na=False,
                nrows = 1e4
            )
        row_ids = raw_patients_df['row_id'].copy()
        source = raw_patients_df['source'].copy()
        data_fields = raw_patients_df.drop(['row_id', 'source'], axis=1)
        patients_df = (
            data_fields
            .map(anyascii)
            .map(lambda x: x.upper())
        )
        patients_df.insert(0, 'source', source)
        patients_df.insert(0, 'row_id', row_ids)

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

    out_file_name = os.path.join(output_folder, output)
    with yaspin(text=f"Writing to {out_file_name}...") as spinner:
        if quiet:
            spinner.stop()
        serialized_hashes = [serialize_bitarray(x) for x in hashed_data]
        patients_df['clk'] = serialized_hashes
        #TODO: optionally print this out for the user to see
        patients_df[['row_id', 'source', 'clk']].to_csv(out_file_name, index=False)

def read_config_file(config, allowed_config_names):
    configuration = yaml.safe_load(open(config))
    observed_config_names = set(configuration.keys())
    unexpected_config_names = observed_config_names - allowed_config_names
    if bool(unexpected_config_names):
        print("The following variables were not expected in the configuration file:")
        print(unexpected_config_names)
        print(" Only the followin variables should be used:")
        print(allowed_config_names)

    #TODO: test to avoid mixing hashing schema with linking schema?
    
    unused_config_names = allowed_config_names - observed_config_names 
    if bool(unexpected_config_names):
        print("The following variables weren't set in the config file:")
        print(unused_config_names)
        print("Default values will be asigned instead.")

    #configuration.setdefault('schema', 'schema.json')
    #configuration.setdefault('secret', 'secret.txt')
    #configuration.setdefault('output', 'out.csv')
    #configuration.setdefault('quiet', True)
    #configuration.setdefault('data_folder', os.path.join(os.getcwd(), "my_files"))
    #configuration.setdefault('schema_folder', os.path.join(os.getcwd(), "schemas"))


#TODO: warn a user if any unexpected names appear in the dictionary!
#TODO: warn a user if a default value is used

    return configuration


def match_CLKs(config):
    """
    Parse a config file and call the underlying CLK matching
    """

    configuration = read_config_file(
            config,
            {'hashes', 'threshold', 'output', 'quiet', 'data_folder', 'output_folder'}
    )

    _match_CLKs(**configuration)

def _match_CLKs(
    hashes = None,
    threshold = 0.9,
    output = 'out.csv',
    quiet = True,
    data_folder = 'my_files',
    output_folder = 'my_files',
    ):

    #TODO: check other lengths
    input_1 = os.path.join(data_folder, hashes[0])
    if len(hashes) == 2:
        self_match = False
        input_2 = os.path.join(data_folder, hashes[1])
    else:
        self_match = True
        input_2 = input_1
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
    linkages_file_name = os.path.join(output_folder, output)
    with open(linkages_file_name, "w") as linkages_file:
        csv_writer = csv.writer(linkages_file)
        csv_writer.writerow([source_1,source_2])
        csv_writer.writerows(relevant_matches)

def self_match_CLKs(config):
    #TODO: delete this function?
    #TODO: data should be called input, not input_1, and it ought to be positional
    #TODO: consider passing config as separate arguments for this

    configuration = yaml.safe_load(open(config))

#TODO: warn a user if any unexpected names appear in the dictionary!
#TODO: warn a user if a default value is used
    _self_match_CLKs(
        data = configuration.get('data'),
        threshold = configuration.get('threshold', 0.9),
        output = configuration.get('output', 'matches.csv'),
        quiet = configuration.get('quiet', True),
        self_match = configuration.get('quiet', True),
        data_folder = configuration.get('data_folder', os.path.join(pwd(), "my_files")),
        schema_folder = configuration.get('schema_folder', os.path.join(pwd(), "schemas"))
        )
