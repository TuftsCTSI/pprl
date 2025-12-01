import filecmp
import json
import pytest
import os

from pprl.tests.templates import basic_error_pattern, basic_test_pattern

def test_no_patient_file_provided():
        basic_error_pattern(TypeError)

@pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
def test_empty_patient_file_provided():
        basic_error_pattern(
                FileNotFoundError,
                patients_1 = "empty_file"
                )

@pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
def test_nonexistent_patient_file_provided():
        basic_error_pattern(
                FileNotFoundError,
                patients_1 = "NONEXISTENT_FILE"
                )

@pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
def test_nonexistent_schema_file_provided():
        basic_error_pattern(
                FileNotFoundError,
                patients_1 = "3_test_patients.csv",
                schema = "NONEXISTENT_FILE"
                )

def test_hashes_export_already_exists():
        basic_error_pattern(
                AssertionError,
                patients_1 = "3_test_patients.csv",
                hashes_1 = "3_test_patients"
                )

def test_linkages_export_already_exists():
        basic_error_pattern(
                AssertionError,
                patients_1 = "3_test_patients.csv",
                linkages = "3_test_patients"
                )

@pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
def test_nonexistent_secret_file_provided():
        basic_error_pattern(
                FileNotFoundError,
                patients_1 = "3_test_patients.csv",
                secret = "NONEXISTENT_FILE"
                )

def test_nonexistent_schema():
    basic_error_pattern(
            FileNotFoundError,
            schema = "NONEXISTENT_FILE.json",
            patients_1 = "20_test_matches_a.csv",
            expected_linkages = ('simple_synthetic', '0-19', 'simple_synthetic', '0-19'),
            )

def test_bad_format_schema():
    for i in range(1,6):
        basic_error_pattern(
                json.JSONDecodeError,
                schema = f"bad_format_{i}.json",
                patients_1 = "20_test_matches_a.csv",
                expected_linkages = ('simple_synthetic', '0-19', 'simple_synthetic', '0-19'),
                )

#TODO: never had a test for output file already exists, either
#TODO: remake this test?
@pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
def test_wrong_schema():
    basic_error_pattern(
            AssertionError,
            patients_1 = "20_test_matches_a.csv",
            expected_linkages = ('simple_synthetic', '0-19', 'simple_synthetic', '0-19'),
            )

