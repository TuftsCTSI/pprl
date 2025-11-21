import filecmp
import pytest
import os

from pprl.tests.templates import basic_error_pattern, basic_test_pattern

def test_purposely_failing():
        assert True == False

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

def test_basic_functionality():
    basic_test_pattern(
            patients_1 = "3_test_patients.csv",
            expected_linkages = 'zoo,zoo\n'
            )

#TODO: never had a test for output file already exists, either
#TODO: remake this test?
@pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
def test_wrong_schema():
    basic_error_pattern(
            AssertionError,
            patients_1 = "20_test_matches_a.csv",
            patients_2 = "20_test_matches_a.csv",
            expected_linkages = 'simple_synthetic,simple_synthetic\n0,0\n1,1\n2,2\n3,3\n4,4\n5,5\n6,6\n7,7\n8,8\n9,9\n10,10\n11,11\n12,12\n13,13\n14,14\n15,15\n16,16\n17,17\n18,18\n19,19\n'
            )

def test_exact_duplicates():
    basic_test_pattern(
            schema = "20_ordering.json",
            patients_1 = "20_test_matches_a.csv",
            patients_2 = "20_test_matches_a.csv",
            expected_linkages = 'simple_synthetic,simple_synthetic\n0,0\n1,1\n2,2\n3,3\n4,4\n5,5\n6,6\n7,7\n8,8\n9,9\n10,10\n11,11\n12,12\n13,13\n14,14\n15,15\n16,16\n17,17\n18,18\n19,19\n'
            )

def test_basic_ordering():
    basic_test_pattern(
            patients_1 = "3_test_patients.csv",
            patients_2 = "rev_3_test_patients.csv",
            expected_linkages = 'zoo,zoo\n0,2\n1,1\n2,0\n'
            )

def test_additional_ordering():
    basic_test_pattern(
            schema = "20_ordering.json",
            patients_1 = "20_test_matches_a.csv",
            patients_2 = "20_test_matches_b.csv",
            expected_linkages = 'simple_synthetic,simple_synthetic\n0,0\n1,2\n2,4\n3,6\n4,8\n5,10\n6,12\n7,14\n8,16\n9,18\n',
            )
