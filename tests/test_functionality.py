from pprl_test_templates import *

def test_no_patient_file_provided():
        basic_error_pattern(TypeError)

def test_nonexistent_patient_file_provided():
        basic_error_pattern(
                FileNotFoundError,
                patients_1 = "NONEXISTENT_FILE"
                )

def test_basic_functionality():
    basic_test_pattern(
            patients_1 = "3_test_patients.csv",
            expected_linkages = 'zoo,zoo\n'
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
