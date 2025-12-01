"""
Test set for Objective 4: Usability

The IRB approves the study design as a team, and we develop a secure process as a team. We should do everything we can to eliminate the possibility of user errors that deviate from this design.
"""

import filecmp
import json
import pytest
import os

from pprl.tests.templates import basic_error_pattern

class TestUsability:
    def test_no_patient_file_provided(capsys):
        """The hashing script should throw an error if no patient identifiers input file is provided."""
        basic_error_pattern(
                capsys,
                TypeError,
                )

    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_nonexistent_patient_file_provided(capsys):
        """The hashing script should throw an error if the patient identifiers input file doesn't exist."""
        basic_error_pattern(capsys,
                FileNotFoundError,
                patients_1 = "NONEXISTENT_FILE"
                )


    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_empty_patient_file_provided(capsys):
        """The hashing script should throw an error if the patient identifiers input file is empty."""
        basic_error_pattern(capsys,
                FileNotFoundError,
                patients_1 = "empty_file"
                )

    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_nonexistent_schema_file_provided(capsys):
        """The hashing script should throw an error if the schema input file doesn't exist."""
        basic_error_pattern(capsys,
                FileNotFoundError,
                patients_1 = "3_test_patients.csv",
                schema = "NONEXISTENT_FILE"
                )

    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_nonexistent_secret_file_provided(capsys):
        """The hashing script should throw an error if the secret input file doesn't exist."""
        basic_error_pattern(capsys,
                FileNotFoundError,
                patients_1 = "3_test_patients.csv",
                secret = "NONEXISTENT_FILE"
                )

    def test_bad_format_schema(capsys):
        """The hashing script should throw an error if the schema input file isn't valid JSON."""
        for i in range(1,6):
            basic_error_pattern(capsys,
                    json.JSONDecodeError,
                    schema = f"bad_format_{i}.json",
                    patients_1 = "20_test_matches_a.csv",
                    expected_linkages = ('simple_synthetic', '0-19', 'simple_synthetic', '0-19'),
                    )


    def test_hashes_export_already_exists(capsys):
        """The hashing script should throw an error if the output hash file already exists."""
        basic_error_pattern(capsys,
                AssertionError,
                patients_1 = "3_test_patients.csv",
                hashes_1 = "3_test_patients"
                )

    def test_linkages_export_already_exists(capsys):
        """The linking script should throw an error if the output linkages file already exists."""
        basic_error_pattern(capsys,
                AssertionError,
                patients_1 = "3_test_patients.csv",
                linkages = "3_test_patients"
                )

    #TODO: never had a test for output file already exists, either
    #TODO: remake this test?
    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_wrong_schema(capsys):
        """The hashing script should throw an error if the input patient identifiers don't follow the schema format."""
        basic_error_pattern(capsys,
                AssertionError,
                patients_1 = "20_test_matches_a.csv",
                expected_linkages = ('simple_synthetic', '0-19', 'simple_synthetic', '0-19'),
                )

