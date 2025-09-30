#import csv
import pandas as pd
import sys

sys.path.append('./src')
import pprl

#TODO: use tempfiles for each test instead

def test_basic_functionality():
    pprl.create_CLKs("tests/configs/create_CLKs.yml", quiet=True)
    pprl.match_CLKs("tests/configs/match_CLKs.yml", quiet=True)
    pprl._match_CLKs(
        "../tests/tmp/CLKs.csv",
        "../tests/tmp/CLKs.csv",
        0.9,
        "../tests/tmp/matches.csv",
        self_match = True,
        quiet=True)

    assert True

    with open('tests/tmp/matches.csv','r') as file:
        output = file.read()
        print(output)
        assert output == 'ZOO,ZOO\n'



    #assert pd.read_csv("./tests/tmp/matches.csv").columns == pd.DataFrame(
            #data={'Zoo': [1, 2, 3],
            #'Zoo': [1, 2, 3]}
            #)

