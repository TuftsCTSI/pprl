import os
import sys
import tempfile

from pprl_test_utilities import *

sys.path.append('./src')
import pprl

def basic_test_pattern(
        patients_1 = None,
        patients_2 = None,
        expected_linkages = None,
        hashes_1 = "hashes1.csv",
        hashes_2 = "hashes2.csv",
        linkages = "linkages.csv",
        schema = "schema.json",
        secret = "secret.txt",
        threshold = 0.9,
        data_folder = None,
        schema_folder = None,
        ):

    current_dir = os.getcwd()
    #parent_dir = os.path.dirname(current_dir)
    if data_folder is None:
        data_folder = os.path.join(current_dir, "tests/data")
    if schema_folder is None:
        schema_folder = os.path.join(current_dir, "tests/schemas")

    with tempfile.TemporaryDirectory() as temp_dir:
        pprl._create_CLKs(
                data_folder = data_folder,
                patients = patients_1,
                schema = schema,
                schema_folder = schema_folder,
                secret = secret,
                output = hashes_1,
                output_folder = temp_dir,
                quiet=True)
        if patients_2 is None:
            hashes = [hashes_1]
        else:
            pprl._create_CLKs(
                    data_folder = data_folder,
                    patients = patients_2,
                    schema_folder = schema_folder,
                    schema = schema,
                    secret = secret,
                    output = hashes_2,
                    output_folder = temp_dir,
                    quiet=True)
            hashes = [hashes_1, hashes_2]
        pprl._match_CLKs(
                data_folder = temp_dir,
                hashes = hashes,
                threshold = threshold,
                output = linkages,
                output_folder = temp_dir,
                quiet=True)
        assert_file_contents(
                os.path.join(temp_dir, linkages),
                expected_linkages
                )

