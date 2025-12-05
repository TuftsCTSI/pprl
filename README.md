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
4. From the main `pprl` directory, run the appropriate `pprl` command.
  - If you're generating hashes from patient data, run `pprl create`.
  - If you're linking hashes in order to determine duplicates, run `pprl match`.
  - Run `pprl` to see a full list of options.
5. Transmit output files according to the study protocol.
6. Delete any sensitive files once they are no longer required.
7. Close or restart the terminal.
