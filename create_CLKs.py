#!./venv/bin/python
import argparse
import sys

sys.path.append('./src')
import pprl

parser = argparse.ArgumentParser()
parser.add_argument("config_file", nargs = '?', default = 'create_CLKs.yml', help = 'The name of the config file (default is create_CLKs.yml)')
args = parser.parse_args()

pprl.create_CLKs(args.config_file)
