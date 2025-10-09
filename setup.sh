#! /bin/bash
set -uo pipefail

#TODO: check to see if python and pip are installed?
#TODO: check to see if the project is already installed?

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

python3 -m venv venv
venv/bin/pip install .

echo 
echo "Setup is complete!"
echo 

