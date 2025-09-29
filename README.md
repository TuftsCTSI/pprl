# TuftsCTSI/pprl
Privacy-preserving record linkage for Tufts CTSI and collaborators

## Setup
1. Run `./setup.sh`

## Usage

### Create cryptographic linkage keys
1. Add the patient identifiers CSV file to `user_files`.

1. Add the secret file to `user_files`.

1. Update `create_CLKs.yml`

1. Run `./create_CLKs.py`. The linkages CSV will be added to `user_files`.

1. Distribute the linkages.

1. Delete any sensitive files from `user_files`.

### Create cryptographic linkage keys
1. Add the patient identifiers CSV files to `user_files`.

1. Update `match_CLKs.yml`

1. Run `./match_CLKs.py`

1. Distribute the matches.

1. Delete any sensitive files from `user_files`.
