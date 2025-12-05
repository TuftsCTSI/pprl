"""
Test set for Objective 2: Privacy

Privacy-preserving record linkage is motivated by a need to protect confidential data.
This code should introduce no unreasonable or easily preventable risk to patient privacy.
"""

import filecmp
import json
import pytest
import os

from pprl.tests.templates import basic_test_pattern, basic_error_pattern

class TestPrivacy:
    """Test if any outputs contain discoverable patient data"""
    #basic_secret_off_by_1.txt

    #TODO: fuzzy matching would be more appropriate here
    def test_link_with_different_secrets(capsys):
        """If data is hashed with 2 different secrets, no matches must be found."""
        basic_error_pattern(capsys, AssertionError,
                schema = "100-patient-schema.json",
                patients_1 = "100-patients-original.csv",
                patients_2 = "100-patients-original.csv",
                secret = "basic_secret.txt",
                secret_2 = "basic_secret_off_by_1.txt",
                expected_linkages = ('100', '1-100', '100', '1-100'),
                )
