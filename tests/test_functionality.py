#import csv
import filecmp
import os
import pandas as pd
import sys
import tempfile

sys.path.append('./src')
import pprl

current_dir = os.getcwd()
#parent_dir = os.path.dirname(current_dir)
test_data_folder = os.path.join(current_dir, "tests/data")
test_schema_folder = os.path.join(current_dir, "tests/schemas")

def assert_file_comparison(file_path_1, file_path_2):
    assert filecmp.cmp(file_path_1, file_path_2)

def assert_file_contents(file_path, expected):
    with open(file_path,'r') as file:
        observed = file.read()
        assert observed == expected

def test_basic_functionality():
    hashes = "hashes.csv"
    linkages = "linkages.csv"
    with tempfile.TemporaryDirectory() as temp_dir:
        pprl._create_CLKs(
                data_folder = test_data_folder,
                patients = "3_test_patients.csv",
                schema_folder = test_schema_folder,
                secret = "secret.txt",
                output_folder = temp_dir,
                output = hashes,
                quiet=True)
        pprl._match_CLKs(
                data_folder = temp_dir,
                hashes = [hashes],
                output_folder = temp_dir,
                output = linkages,
                quiet=True)
        assert_file_contents(
                os.path.join(temp_dir, linkages),
                'zoo,zoo\n'
                )

def test_basic_ordering():
    hashes_1 = "hashes1.csv"
    hashes_2 = "hashes2.csv"
    linkages = "linkages.csv"
    schema = "schema.json"
    secret = "secret.txt"
    patients = "3_test_patients.csv"
    with tempfile.TemporaryDirectory() as temp_dir:
        pprl._create_CLKs(
                data_folder = test_data_folder,
                patients = patients,
                schema = schema,
                secret = secret,
                output = hashes_1,
                output_folder = temp_dir,
                quiet=True)
        pprl._create_CLKs(
                data_folder = test_data_folder,
                patients = "rev_3_test_patients.csv",
                schema = "schema.json",
                secret = "secret.txt",
                output = hashes_2,
                output_folder = temp_dir,
                quiet=True)
        pprl._match_CLKs(
                data_folder = temp_dir,
                hashes = [hashes_1, hashes_2],
                threshold = 0.9,
                output = linkages,
                output_folder = temp_dir,
                quiet=True)
        assert_file_contents(
                os.path.join(temp_dir, linkages),
                'zoo,zoo\n0,2\n1,1\n2,0\n'
                )

def test_additional_ordering():
    schema = "20_ordering.json"
    secret = "secret.txt"
    hashes_1 = "hashes1.csv"
    hashes_2 = "hashes2.csv"
    linkages = "linkages.csv"
    with tempfile.TemporaryDirectory() as temp_dir:
        pprl._create_CLKs(
                data_folder = test_data_folder,
                schema_folder = test_schema_folder,
                patients = "20_test_matches_a.csv",
                schema = schema,
                secret = secret,
                output_folder = temp_dir,
                output = hashes_1,
                quiet=True)
        pprl._create_CLKs(
                data_folder = test_data_folder,
                schema_folder = test_schema_folder,
                patients = "20_test_matches_b.csv",
                schema = schema,
                secret = secret,
                output_folder = temp_dir,
                output = hashes_2,
                quiet=True)
        pprl._match_CLKs(
                hashes = [hashes_1, hashes_2],
                data_folder = temp_dir,
                output_folder = temp_dir,
                output = linkages,
                quiet=True)
        assert_file_contents(
                os.path.join(temp_dir, linkages),
                'simple_synthetic,simple_synthetic\n0,0\n1,2\n2,4\n3,6\n4,8\n5,10\n6,12\n7,14\n8,16\n9,18\n'
                )
