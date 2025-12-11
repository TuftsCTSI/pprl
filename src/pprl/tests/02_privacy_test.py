"""
Test set for Objective 2: Privacy

Privacy-preserving record linkage is motivated by a need to protect confidential data.
This code should introduce no unreasonable or easily preventable risk to patient privacy.
"""

import filecmp
import json
import pytest

from pprl.tests.templates import basic_test_pattern, basic_error_pattern, compare_hashes

class TestPrivacy:
    """Test if any outputs contain discoverable patient data"""
    #basic_secret_off_by_1.txt

    #TODO: fuzzy matching would be more appropriate here
    def test_link_with_different_secrets(capsys):
        """No matches can be returned without identical secrets"""
        basic_error_pattern(capsys, AssertionError,
                schema = "100-patient-schema.json",
                patients_1 = "100-patients-original.csv",
                patients_2 = "100-patients-original.csv",
                secret = "basic_secret.txt",
                secret_2 = "basic_secret_off_by_1.txt",
                expected_linkages = ('100', '1-100', '100', '1-100'),
                )

    @pytest.mark.xfail(xfail_strict = True, reason="For reasons unknown, the value hovers between 0.70 and 0.85.")
    def test_hash_variation(capsys):
        """Altering one bit in the secret must alter ~50% of bits in the hash"""
        compare_hashes(capsys,
                schema = "100-patient-schema.json",
                patients_1 = "100-patients-original.csv",
                patients_2 = "100-patients-original.csv",
                secret = "basic_secret.txt",
                secret_2 = "basic_secret_off_by_1.txt",
                #lower_bound = 0.40,
                upper_bound = 0.60,
                )
