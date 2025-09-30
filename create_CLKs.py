import argparse
import sys

sys.path.append('./src')
import pprl

parser = argparse.ArgumentParser()
# optionally leave out
parser.add_argument('-c', '--config', type=str, default= 'create_CLKs.yml', help='The name of the config file (default is create_CLKs.yml)')
parser.add_argument('-q', '--quiet', action='store_true', help='(Disables any visual updates and progress bars)')
args = parser.parse_args()

pprl.create_CLKs(**vars(args))
