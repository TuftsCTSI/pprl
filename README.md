# TuftsCTSI/pprl
Privacy-preserving record linkage for Tufts CTSI and collaborators

## Setup
1. Ensure that you have Python with pip installed.
1. Clone the repo: `git clone https://github.com/TuftsCTSI/pprl`
1. Open the new directory: `cd pprl`
1. Run the setup script: `./setup.sh`
1. Verify your setup by running the test suite: `./runtests.sh`

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
