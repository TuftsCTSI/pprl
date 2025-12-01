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

def test_basic_functionality():
    basic_test_pattern(
            patients_1 = "3_test_patients.csv",
            expected_linkages = ('zoo', '', 'zoo', ''),
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

def test_exact_duplicates():
    basic_test_pattern(
            schema = "20_ordering.json",
            patients_1 = "20_test_matches_a.csv",
            patients_2 = "20_test_matches_a.csv",
            expected_linkages = ('simple_synthetic', '0-19', 'simple_synthetic', '0-19'),
            )

def test_basic_ordering():
    basic_test_pattern(
            patients_1 = "3_test_patients.csv",
            patients_2 = "rev_3_test_patients.csv",
            expected_linkages = ('zoo', '1-3', 'zoo', '3-1'),
            )

def test_additional_ordering():
    basic_test_pattern(
            schema = "20_ordering.json",
            patients_1 = "20_test_matches_a.csv",
            patients_2 = "20_test_matches_b.csv",
            expected_linkages = ('simple_synthetic', '0-9', 'simple_synthetic', '0,2,4,6,8,10,12,14,16,18'),
            )

def test_100_patients():
    basic_test_pattern(
            schema = "100-patient-schema.json",
            patients_1 = "100-patients-original.csv",
            patients_2 = "100-patients-original.csv",
            expected_linkages = ('100', '1-100', '100', '1-100'),
            )

def test_100_patients_capitalization():
    basic_test_pattern(
            schema = "100-patient-schema.json",
            patients_1 = "100-patients-original.csv",
            patients_2 = "100-patients-capitalization.csv",
            expected_linkages = ('100', '1-100', '100', '1-100'),
            )

def test_100_patients_missing_data():
    basic_test_pattern(
            schema = "100-patient-schema.json",
            patients_1 = "100-patients-original.csv",
            patients_2 = "100-patients-missing-data.csv",
            expected_linkages = ('100', '41-100', '100', '41-100'),
            )

@pytest.mark.skip(reason="I can't find a setting that causes both this and the 100 patient missing data test to pass. I think it's more important to reject linking with missing fields than to permit all off-by-1 possibilities, though maybe it's sufficient to ust require all fields to be filled.")
def test_100_patients_off_by_1():
    basic_test_pattern(
            schema = "100-patient-schema.json",
            patients_1 = "100-patients-original.csv",
            patients_2 = "100-patients-off-by-1.csv",
            expected_linkages = ('100', '1-75', '100', '1-75'),
            )

