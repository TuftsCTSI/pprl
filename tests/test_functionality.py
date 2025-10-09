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
    with tempfile.TemporaryDirectory() as temp_dir:
        pprl._create_CLKs(
                data_folder = test_data_folder,
                patients = "3_test_patients.csv",
                schema_folder = test_schema_folder,
                secret = "secret.txt",
                output_folder = temp_dir,
                output = "CLKs.csv",
                quiet=True)
        pprl._match_CLKs(
                data_folder = temp_dir,
                hashes = ["CLKs.csv"],
                output_folder = temp_dir,
                output = "abc",
                quiet=True)
        assert_file_contents(
                os.path.join(temp_dir, "abc"),
                'zoo,zoo\n'
                )

def test_basic_ordering():
    with tempfile.TemporaryDirectory() as temp_dir:
        pprl._create_CLKs(
                data_folder = test_data_folder,
                patients = "3_test_patients.csv",
                schema = "schema.json",
                secret = "secret.txt",
                output = "CLKsa1.csv",
                output_folder = temp_dir,
                quiet=True)
        pprl._create_CLKs(
                data_folder = test_data_folder,
                patients = "rev_3_test_patients.csv",
                schema = "schema.json",
                secret = "secret.txt",
                output = "CLKsa2.csv",
                output_folder = temp_dir,
                quiet=True)
        pprl._match_CLKs(
                data_folder = temp_dir,
                hashes = ['CLKsa1.csv', 'CLKsa2.csv'],
                threshold = 0.9,
                output = 'matches.csv',
                output_folder = temp_dir,
                quiet=True)
        assert_file_contents(
                os.path.join(temp_dir, "matches.csv"),
                'zoo,zoo\n0,2\n1,1\n2,0\n'
                )

def test_additional_ordering():
    with tempfile.TemporaryDirectory() as temp_dir:
        pprl._create_CLKs(
                data_folder = test_data_folder,
                schema_folder = test_data_folder,
                patients = "20_test_matches_a.csv",
                schema = "20_ordering.json",
                secret = "secret.txt",
                output_folder = temp_dir,
                output = 'asd1',
                quiet=True)
        pprl._create_CLKs(
                data_folder = 'tests/data',
                schema_folder = 'tests/schemas',
                patients = "20_test_matches_b.csv",
                schema = "20_ordering.json",
                secret = "secret.txt",
                output_folder = temp_dir,
                output = 'asd2',
                quiet=True)
        pprl._match_CLKs(
                hashes = ['asd1','asd2'],
                data_folder = temp_dir,
                threshold = 0.9,
                output_folder = temp_dir,
                output = "qmatches.csv", # TODO: tempfile
                quiet=True)
        assert_file_contents(
                os.path.join(temp_dir, "qmatches.csv"),
                'simple_synthetic,simple_synthetic\n0,0\n1,2\n2,4\n3,6\n4,8\n5,10\n6,12\n7,14\n8,16\n9,18\n'
                )
