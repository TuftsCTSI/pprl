import sys

sys.path.append('./src')
import pprl

def test_basic_functionality():
    pprl.create_CLKs(config="tests/configs/create_CLKs.yml", quiet=True)
    pprl.match_CLKs(config="tests/configs/match_CLKs.yml", quiet=True)
    assert True

