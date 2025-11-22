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
            patients_2 = "20_test_matches_a.csv",
            expected_linkages = 'simple_synthetic,simple_synthetic\n0,0\n1,1\n2,2\n3,3\n4,4\n5,5\n6,6\n7,7\n8,8\n9,9\n10,10\n11,11\n12,12\n13,13\n14,14\n15,15\n16,16\n17,17\n18,18\n19,19\n'
            )

def test_bad_format_schema():
    for i in range(1,6):
        basic_error_pattern(
                json.JSONDecodeError,
                schema = f"bad_format_{i}.json",
                patients_1 = "20_test_matches_a.csv",
                patients_2 = "20_test_matches_a.csv",
                expected_linkages = 'simple_synthetic,simple_synthetic\n0,0\n1,1\n2,2\n3,3\n4,4\n5,5\n6,6\n7,7\n8,8\n9,9\n10,10\n11,11\n12,12\n13,13\n14,14\n15,15\n16,16\n17,17\n18,18\n19,19\n'
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
            expected_linkages = 'zoo,zoo\n1,3\n2,2\n3,1\n'
            )

def test_additional_ordering():
    basic_test_pattern(
            schema = "20_ordering.json",
            patients_1 = "20_test_matches_a.csv",
            patients_2 = "20_test_matches_b.csv",
            expected_linkages = 'simple_synthetic,simple_synthetic\n0,0\n1,2\n2,4\n3,6\n4,8\n5,10\n6,12\n7,14\n8,16\n9,18\n',
            )

def test_100_patients():
    basic_test_pattern(
            schema = "100-patient-schema.json",
            patients_1 = "100-patients-original.csv",
            patients_2 = "100-patients-original.csv",
            expected_linkages = '100,100\n1,1\n2,2\n3,3\n4,4\n5,5\n6,6\n7,7\n8,8\n9,9\n10,10\n11,11\n12,12\n13,13\n14,14\n15,15\n16,16\n17,17\n18,18\n19,19\n20,20\n21,21\n22,22\n23,23\n24,24\n25,25\n26,26\n27,27\n28,28\n29,29\n30,30\n31,31\n32,32\n33,33\n34,34\n35,35\n36,36\n37,37\n38,38\n39,39\n40,40\n41,41\n42,42\n43,43\n44,44\n45,45\n46,46\n47,47\n48,48\n49,49\n50,50\n51,51\n52,52\n53,53\n54,54\n55,55\n56,56\n57,57\n58,58\n59,59\n60,60\n61,61\n62,62\n63,63\n64,64\n65,65\n66,66\n67,67\n68,68\n69,69\n70,70\n71,71\n72,72\n73,73\n74,74\n75,75\n76,76\n77,77\n78,78\n79,79\n80,80\n81,81\n82,82\n83,83\n84,84\n85,85\n86,86\n87,87\n88,88\n89,89\n90,90\n91,91\n92,92\n93,93\n94,94\n95,95\n96,96\n97,97\n98,98\n99,99\n100,100\n',
            )

def test_100_patients_capitalization():
    basic_test_pattern(
            schema = "100-patient-schema.json",
            patients_1 = "100-patients-original.csv",
            patients_2 = "100-patients-capitalization.csv",
            expected_linkages = '100,100\n1,1\n2,2\n3,3\n4,4\n5,5\n6,6\n7,7\n8,8\n9,9\n10,10\n11,11\n12,12\n13,13\n14,14\n15,15\n16,16\n17,17\n18,18\n19,19\n20,20\n21,21\n22,22\n23,23\n24,24\n25,25\n26,26\n27,27\n28,28\n29,29\n30,30\n31,31\n32,32\n33,33\n34,34\n35,35\n36,36\n37,37\n38,38\n39,39\n40,40\n41,41\n42,42\n43,43\n44,44\n45,45\n46,46\n47,47\n48,48\n49,49\n50,50\n51,51\n52,52\n53,53\n54,54\n55,55\n56,56\n57,57\n58,58\n59,59\n60,60\n61,61\n62,62\n63,63\n64,64\n65,65\n66,66\n67,67\n68,68\n69,69\n70,70\n71,71\n72,72\n73,73\n74,74\n75,75\n76,76\n77,77\n78,78\n79,79\n80,80\n81,81\n82,82\n83,83\n84,84\n85,85\n86,86\n87,87\n88,88\n89,89\n90,90\n91,91\n92,92\n93,93\n94,94\n95,95\n96,96\n97,97\n98,98\n99,99\n100,100\n',
            )

def test_100_patients_missing_data():
    basic_test_pattern(
            schema = "100-patient-schema.json",
            patients_1 = "100-patients-original.csv",
            patients_2 = "100-patients-missing-data.csv",
            expected_linkages = '100,100\n41,41\n42,42\n43,43\n44,44\n45,45\n46,46\n47,47\n48,48\n49,49\n50,50\n51,51\n52,52\n53,53\n54,54\n55,55\n56,56\n57,57\n58,58\n59,59\n60,60\n61,61\n62,62\n63,63\n64,64\n65,65\n66,66\n67,67\n68,68\n69,69\n70,70\n71,71\n72,72\n73,73\n74,74\n75,75\n76,76\n77,77\n78,78\n79,79\n80,80\n81,81\n82,82\n83,83\n84,84\n85,85\n86,86\n87,87\n88,88\n89,89\n90,90\n91,91\n92,92\n93,93\n94,94\n95,95\n96,96\n97,97\n98,98\n99,99\n100,100\n',
            )

