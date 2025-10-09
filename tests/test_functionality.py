#import csv
import filecmp
import os
import pandas as pd
import sys
import tempfile

sys.path.append('./src')
import pprl

def assert_file_comparison(file_path_1, file_path_2):
    assert filecmp.cmp(file_path_1, file_path_2)

def assert_file_contents(file_path, expected):
    with open(file_path,'r') as file:
        observed = file.read()
        assert observed == expected

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

def test_basic_functionality():
    basic_test_pattern(
                patients_1 = "3_test_patients.csv",
                expected_linkages = 'zoo,zoo\n'
            )

def test_basic_ordering():
    basic_test_pattern(
                patients_1 = "3_test_patients.csv",
                patients_2 = "rev_3_test_patients.csv",
                expected_linkages = 'zoo,zoo\n0,2\n1,1\n2,0\n'
            )

def test_additional_ordering():
    basic_test_pattern(
                schema = "20_ordering.json",
                patients_1 = "20_test_matches_a.csv",
                patients_2 = "20_test_matches_b.csv",
                expected_linkages = 'simple_synthetic,simple_synthetic\n0,0\n1,2\n2,4\n3,6\n4,8\n5,10\n6,12\n7,14\n8,16\n9,18\n',
            )
