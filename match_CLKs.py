import anonlink
import bitarray
import csv
import io
import os
import pandas as pd
import sys

import clkhash
from clkhash.serialization import deserialize_bitarray

#pd.set_option('display.max_columns', None)

import yaml
config_file_name = "match_CLKs.yml"
config = yaml.safe_load(open(config_file_name))

user_file_dir = "my_files"
input_1 = os.path.join(user_file_dir, config["input_1"])
input_2 = os.path.join(user_file_dir, config["input_2"])
df_1 = pd.read_csv(input_1)
df_2 = pd.read_csv(input_2)

hashed_data_1 = [deserialize_bitarray(x) for x in df_1['clk']]
hashed_data_2 = [deserialize_bitarray(x) for x in df_2['clk']]

source_1 = df_1['source'][0]
source_2 = df_2['source'][0]

threshold = config["threshold"]

results_candidate_pairs = anonlink.candidate_generation.find_candidate_pairs(
        [hashed_data_1, hashed_data_2],
        anonlink.similarities.dice_coefficient,
        threshold
)
solution = anonlink.solving.greedy_solve(results_candidate_pairs)
found_matches = sorted(list([id_1, id_2] for ((_, id_1), (_, id_2)) in solution))

#TODO: If sources are 2 collaborating sites, produce a separate output file for each source.
# That way, each group receives only presence/absence of link
# This could be toggled in the config file, which I could manually prepare for each site.
linkages_file_name = os.path.join(user_file_dir, config["matches"])
with open(linkages_file_name, "w") as linkages_file:
    csv_writer = csv.writer(linkages_file)
    csv_writer.writerow([source_1,source_2])
    csv_writer.writerows(found_matches)
