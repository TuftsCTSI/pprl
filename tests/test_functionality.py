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
        assert output == 'zoo,zoo\n'

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
        quiet=True)

    with open('tests/tmp/matches.csv','r') as file:
        output = file.read()
        print(output)
        assert output == 'zoo,zoo\n0,2\n1,1\n2,0\n'

def test_additional_ordering():
    pprl._create_CLKs(
        "../tests/populations/20_test_matches_a.csv",
        "../tests/schemas/20_ordering.json",
        "../tests/secrets/secret.txt",
        "../tests/tmp/CLKsa3.csv",
            quiet=True)
    pprl._create_CLKs(
        "../tests/populations/20_test_matches_b.csv",
        "../tests/schemas/20_ordering.json",
        "../tests/secrets/secret.txt",
        "../tests/tmp/CLKsa4.csv",
            quiet=True)
    pprl._match_CLKs(
        "../tests/tmp/CLKsa3.csv",
        "../tests/tmp/CLKsa4.csv",
        0.9,
        "../tests/tmp/qmatches.csv",
        quiet=True)

    with open('tests/tmp/qmatches.csv','r') as file:
        output = file.read()
        print(output)
        assert output == 'simple_synthetic,simple_synthetic\n0,0\n1,2\n2,4\n3,6\n4,8\n5,10\n6,12\n7,14\n8,16\n9,18\n'
