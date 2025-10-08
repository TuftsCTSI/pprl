#import csv
import pandas as pd
import sys

sys.path.append('./src')
import pprl

#TODO: use tempfiles for each test instead

def test_basic_functionality():
    pprl.create_CLKs("tests/configs/create_CLKs.yml", quiet=True)
    #pprl.match_CLKs("tests/configs/match_CLKs.yml", quiet=True)
    pprl._match_CLKs(
        "../tests/tmp/CLKs.csv",
        "../tests/tmp/CLKs.csv",
        0.9,
        "../tests/tmp/matches.csv",
        self_match = True,
        quiet=True)

    with open('tests/tmp/matches.csv','r') as file:
        output = file.read()
        print(output)
        assert output == 'ZOO,ZOO\n'

def test_basic_ordering():
    pprl._create_CLKs(
        "../tests/populations/3_test_patients.csv",
        "../tests/schemas/schema.json",
        "../tests/secrets/secret.txt",
        "../tests/tmp/CLKsa1.csv",
            quiet=True)
    pprl._create_CLKs(
        "../tests/populations/rev_3_test_patients.csv",
        "../tests/schemas/schema.json",
        "../tests/secrets/secret.txt",
        "../tests/tmp/CLKsa2.csv",
            quiet=True)
    pprl._match_CLKs(
        "../tests/tmp/CLKsa1.csv",
        "../tests/tmp/CLKsa2.csv",
        0.9,
        "../tests/tmp/matches.csv",
        self_match = True,
        quiet=True)

    with open('tests/tmp/matches.csv','r') as file:
        output = file.read()
        print(output)
        assert output == 'ZOO,ZOO\n0,2\n'
