import pandas as pd
import sys

sys.path.append('./src')
import pprl

def test_basic_functionality():
    pprl.create_CLKs("tests/configs/create_CLKs.yml", quiet=True)
    pprl.match_CLKs("tests/configs/match_CLKs.yml", quiet=True)

    assert True

    #assert pd.read_csv("./tests/tmp/matches.csv").columns == pd.DataFrame(
            #data={'Zoo': [1, 2, 3],
            #'Zoo': [1, 2, 3]}
            #)


