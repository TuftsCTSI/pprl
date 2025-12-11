"""
Test set for Objective 4: Usability

The IRB approves the study design as a team, and we develop a secure process as a team.
We should do everything we can to eliminate the possibility of user errors that deviate from this design.
"""

import filecmp
import json
import pytest

from pprl.tests.templates import basic_error_pattern

class TestUsability:

    #TODO: tests for missing YAML config files?
    #TODO: tests for nonexistent YAML config files
    #TODO: tests for empty YAML config files / no discernable options
    #TODO: tests for YAML files with wrong format
    #TODO: tests for YAML files with nonexistent options

    """Test that proper procedure is clear and that unintended behaviors cannot occur"""
    def test_no_patient_file_provided(capsys):
        """Hashing without a patient identifiers filename must throw an error"""
        basic_error_pattern(
                capsys,
                TypeError,
                )

    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_nonexistent_patient_file_provided(capsys):
        """Hashing with a nonexistent patient identifiers file must throw an error"""
        basic_error_pattern(capsys,
                FileNotFoundError,
                patients_1 = "NONEXISTENT_FILE"
                )


    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_empty_patient_file_provided(capsys):
        """Hashing with an empty patient identifiers file must throw an error"""
        basic_error_pattern(capsys,
                FileNotFoundError,
                patients_1 = "empty_file"
                )

    #TODO: patient CSV file, properly formatted, with no rows

    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_nonexistent_schema_file_provided(capsys):
        """Hashing with a nonexistent schema file must throw an error"""
        basic_error_pattern(capsys,
                FileNotFoundError,
                patients_1 = "3_test_patients.csv",
                schema = "NONEXISTENT_FILE"
                )

    #TODO: empty, misformat, missing entries

    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_nonexistent_secret_file_provided(capsys):
        """Hashing with a nonexistent secret file must throw an error"""
        basic_error_pattern(capsys,
                FileNotFoundError,
                patients_1 = "3_test_patients.csv",
                secret = "NONEXISTENT_FILE"
                )

    def test_empty_secret_file_provided(capsys):
        """Hashing with an empty secret file must throw an error"""
        basic_error_pattern(capsys,
                ValueError,
                patients_1 = "3_test_patients.csv",
                secret = "empty_file"
                )

    #TODO: other filters on secret (length, characters?)

    def test_bad_format_schema(capsys):
        """Hashing with an invalid schema file must throw an error"""
        for i in range(1,6):
            basic_error_pattern(capsys,
                    json.JSONDecodeError,
                    schema = f"bad_format_{i}.json",
                    patients_1 = "20_test_matches_a.csv",
                    expected_linkages = ('simple_synthetic', '0-19', 'simple_synthetic', '0-19'),
                    )

    def test_hashes_export_already_exists(capsys):
        """Hashing must throw an error if output already exists"""
        basic_error_pattern(capsys,
                AssertionError,
                patients_1 = "3_test_patients.csv",
                hashes_1 = "3_test_patients"
                )

    #TODO: Add all tests similar to this (outfile already exists)
    def test_linkages_export_already_exists(capsys):
        """Linking must throw an error if output already exists"""
        basic_error_pattern(capsys,
                AssertionError,
                patients_1 = "3_test_patients.csv",
                linkages = "3_test_patients"
                )

    #TODO: never had a test for output file already exists, either
    #TODO: remake this test?
    @pytest.mark.skip(reason="For the user's sake, we now print a custom message and exit, rather than throw an error")
    def test_wrong_schema(capsys):
        """Hashing must throw an error if the patient identifiers don't follow the schema"""
        basic_error_pattern(capsys,
                AssertionError,
                patients_1 = "20_test_matches_a.csv",
                expected_linkages = ('simple_synthetic', '0-19', 'simple_synthetic', '0-19'),
                )

