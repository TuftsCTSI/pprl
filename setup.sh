#! /bin/bash
set -uxo pipefail

python3 -m venv venv
venv/bin/pip install .
