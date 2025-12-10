# TuftsCTSI/pprl
Privacy-preserving record linkage for Tufts CTSI and collaborators

## Setup

Complete the following steps to use the software on your machine.

1. Ensure that you have Python installed. A version of 3.12 (released Oct 2, 2023) or higher is required.
2. [Install uv](https://github.com/astral-sh/uv?tab=readme-ov-file#installation), a Python package manager.
3. Copy this PPRL software to your computer. If you have `git` installed, try running `git clone https://github.com/TuftsCTSI/pprl`.
4. Install dependencies with `uv sync`. You'll need to run this command within the newly-created folder (`cd pprl`, depending on your system).

Proceed to the Usage section to test your installation, or to run the software.

Typical users will only need to do complete these steps once. If you're updating your version of `TuftsCTSI/pprl`, you should pull the latest changes and rerun `uv sync`.

## Usage

1. Run `source .venv/bin/activate` to add the `pprl` command to your terminal.
    - This command must be run directly in the top-level `pprl` folder.
    - You should see your prompt update, and you can test the effect by running `pprl`. 
2. At this point, no configuration files are in place, but if you'd like to test out the installation, `pprl test` and `pprl report` are available.
3. If you received any configuration files from us, place them each directly to the `my_files` folder. It should be a subdirectory of the main `pprl` folder.
4. Depending on your role, you might have generated an input file as well. This should also be placed in `my_files`.
5. Depending on your role, run the appropriate `pprl` command:
    - If you're generating hashes from patient data, run `pprl hash`.
    - If you're linking hashes in order to determine duplicates, run `pprl link`.
    - Run `pprl` to see a full list of options.
6. Transmit output files according to the study protocol. Any output files will be created in the `my_files` directory.
7. Remember to delete any sensitive files once they are no longer required. 
8. Close or restart the terminal.
