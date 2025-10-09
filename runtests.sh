#! /bin/bash
set -uo pipefail

if [ ! -f venv/bin/pytest ]; then
	echo
	echo "ERROR:"
	echo "    The file venv/bin/pytest couldn't be found!"
	echo "    Be sure to run ./setup.sh first, resolving any errors that occur."
	echo

	exit
fi

venv/bin/pytest

