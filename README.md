# TuftsCTSI/pprl
Privacy-preserving record linkage for Tufts CTSI and collaborators

## Setup
1. Ensure that you have Python installed.
2. [Install uv](https://github.com/astral-sh/uv?tab=readme-ov-file#installation), a Python package manager.
3. Clone this PPRL repository: `git clone https://github.com/TuftsCTSI/pprl`
4. Open the newly created folder: `cd pprl`
5. Install dependencies with `uv sync`.
6. Run `source .venv/bin/activate`.
7. Verify your setup by running the test suite: `pprl test`
8. Close or restart the terminal (or proceed to the Usage section).

## Usage

1. Add all configuration files to the `my_files` subdirectory.
2. Depending on your role, you might have generated an input file. This should also be placed in `my_files`.
3. Run `source .venv/bin/activate`, if you haven't already.
4. Run the proper `pprl` command from the main `pprl` directory.
5. Delete any sensitive files once they are no longer required.
6. Close or restart the terminal.

### Create cryptographic linkage keys
1. Add the patient identifiers CSV file to `user_files`.
2. Add the secret file to `user_files`.
3. Update `create_CLKs.yml`
4. activate the virtual environment with: `source .venv/bin/activate`
5. Run `pprl create`. The linkages CSV will be added to `user_files`.
6. Distribute the linkages.
7. Delete any sensitive files from `user_files`.

### Create cryptographic linkage keys
1. Add the patient identifiers CSV files to `user_files`.
2. Update `match_CLKs.yml`
3. activate the virtual environment with: `source .venv/bin/activate`
4. Run `pprl match`
5. Distribute the matches.
6. Delete any sensitive files from `user_files`.
