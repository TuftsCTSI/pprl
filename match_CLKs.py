#!./venv/bin/python
import argparse
import sys

sys.path.append('./src')
import pprl

parser = argparse.ArgumentParser()
parser.add_argument("config_file", nargs = '?', default = 'match_CLKs.yml', help = 'The name of the config file (default is match_CLKs.yml)')
args = parser.parse_args()

pprl.match_CLKs(args.config_file)

