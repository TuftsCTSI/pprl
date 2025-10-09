#import csv
import os
import pandas as pd
import sys
import tempfile

sys.path.append('./src')
import pprl

current_dir = os.getcwd()
parent_dir = os.path.dirname(current_dir)
test_data_dir = os.path.join(os.getcwd(), "tests/data")
schema_data_dir = os.path.join(os.getcwd(), "tests/schemas")

#TODO: use tempfiles for each test instead

def test_basic_functionality():

    pprl.create_CLKs("tests/configs/create_CLKs.yml")
    pprl._match_CLKs(
        data_dir = 'tests/data/',
        hashes = ["CLKs.csv"],
        threshold=  0.9,
        output = "abc",
        quiet=True)

    with open('tests/data/abc','r') as file:
        output = file.read()
        assert output == 'zoo,zoo\n'

def test_basic_ordering():
    pprl._create_CLKs(
        data_dir = 'tests/data/',
        patients = "3_test_patients.csv",
        schema = "schema.json",
        secret = "secret.txt",
        output = "CLKsa1.csv",
            quiet=True)
    pprl._create_CLKs(
        data_dir = 'tests/data/',
        patients = "rev_3_test_patients.csv",
        schema = "schema.json",
        secret = "secret.txt",
        output = "CLKsa2.csv",
            quiet=True)
    pprl._match_CLKs(
        data_dir = 'tests/data/',
        hashes = ['CLKsa1.csv', 'CLKsa2.csv'],
        threshold = 0.9,
        output = 'matches.csv',
        quiet=True)

    with open('tests/data/matches.csv','r') as file:
        output = file.read()
        print(output)
        assert output == 'zoo,zoo\n0,2\n1,1\n2,0\n'

def test_additional_ordering():

    pprl._create_CLKs(
        data_dir = 'tests/data',
        schema_dir = 'tests/schemas',
        patients = "20_test_matches_a.csv",
        schema = "20_ordering.json",
        secret = "secret.txt",
        output = 'asd1',
            quiet=True)
    pprl._create_CLKs(
        data_dir = 'tests/data',
        schema_dir = 'tests/schemas',
        patients = "20_test_matches_b.csv",
        schema = "20_ordering.json",
        secret = "secret.txt",
        output = 'asd2',
            quiet=True)
    pprl._match_CLKs(
            hashes = ['asd1','asd2'],
        data_dir = test_data_dir,
        threshold = 0.9,
        output = "qmatches.csv", # TODO: tempfile
        quiet=True)
    #TODO: use tempfile, or otherwise delete any files created during tests (Pytests can automate?)
    #TODO: add an aditional outdir, especially for tests

    with open('tests/data/qmatches.csv','r') as file:
        output = file.read()
        print(output)
        assert output == 'simple_synthetic,simple_synthetic\n0,0\n1,2\n2,4\n3,6\n4,8\n5,10\n6,12\n7,14\n8,16\n9,18\n'
