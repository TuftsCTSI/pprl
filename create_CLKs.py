import argparse
import io
import json
import os
import pandas as pd
import yaml

from anyascii import anyascii

import clkhash
from clkhash import clk
from clkhash.schema import from_json_dict
from clkhash.serialization import serialize_bitarray
from yaspin import yaspin

schema_file_dir = "schemas"
user_file_dir = "my_files"
config_file_name = "create_CLKs.yml"

config = yaml.safe_load(open(config_file_name))

parser = argparse.ArgumentParser()
parser.add_argument('-q', '--quiet', action='store_true', help='(Disables any visual updates and progress bars)')
args = parser.parse_args()

#TODO: check for file existence, validity, etc.

import sys

with yaspin(text="Reading from files and preprocessing...") as spinner:
    if args.quiet:
        spinner.stop()
    # Linking schema
    schema_file_name = os.path.join(schema_file_dir, config["schema"])
    with open(schema_file_name, 'r') as f:
        schema_dict = json.load(f)
        schema = from_json_dict(schema_dict)

    # Secret
    secret_file_name = os.path.join(user_file_dir, config["secret"])
    with open(secret_file_name, 'r') as secret_file:
        secret = secret_file.read()

    # Patient identifiers
    patients_file_name = os.path.join(user_file_dir, config["records"])
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

hashed_data = clk.generate_clk_from_csv(patients_str, secret, schema, progress_bar = not args.quiet)

out_file_name = os.path.join(user_file_dir, config["output"])
with yaspin(text=f"Writing to {out_file_name}...") as spinner:
    if args.quiet:
        spinner.stop()
    serialized_hashes = [serialize_bitarray(x) for x in hashed_data]
    patients_df['clk'] = serialized_hashes
    #TODO: optionally print this out for the user to see
    patients_df[['row_id', 'source', 'clk']].to_csv(out_file_name, index=False)
