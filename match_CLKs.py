import argparse
import sys

sys.path.append('./src')
import pprl

parser = argparse.ArgumentParser()
parser.add_argument('-c', '--config', type=str, default= 'match_CLKs.yml', help='The name of the config file (default is match_CLKs.yml)')
parser.add_argument('-q', '--quiet', action='store_true', help='(Disables any visual updates and progress bars)')
args = parser.parse_args()
config_file_name = args.config

pprl.match_CLKs(**vars(args))
