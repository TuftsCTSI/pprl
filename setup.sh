#!/usr/bin/env bash
set -uo pipefail

current_directory=${PWD##*/} # fails for root directory: /
if [ "$current_directory" != "pprl" ]; then
	echo
	echo "ERROR:"
	echo "    The current directory is not named 'pprl'!"
	echo "    Be sure to run ./setup.sh inside the pprl directory."
	echo

	exit
fi

echo
echo "Running setup..."
echo
echo "Checking for python & pip or uv..."

UV=false
PY=""
PIP=""

if command -v uv &>/dev/null 2>&1; then
	echo "uv is installed, using this for installation"
	UV=true
elif command -v python3 &>/dev/null 2>&1; then
	echo "Python3 is installed."
	PY="python3"
elif command -v python &>/dev/null 2>&1; then
	echo "Python is installed."
	PY="python"
else
	echo "ERROR: Need python or uv installed and on path."
	exit 1
fi

if not $UV; then
	if command -v pip3 >/dev/null 2>&1; then
		PIP="pip3"
	elif command -v pip >/dev/null 2>&1; then
		PIP="pip"
	else
		echo "ERROR: Need pip or uv installed and on path"
		exit 1
	fi
	echo "installing uv via $PIP"
	$PIP install uv
fi

echo "creating venv"
uv venv
source .venv/bin/activate
uv sync

echo
echo "Setup is complete!"
echo

