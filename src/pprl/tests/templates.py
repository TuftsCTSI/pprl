import os
import pytest
import tempfile
from pathlib import Path
from pprl.tests.utilities import (
    assert_file_comparison,
    assert_file_contents,
    ranges_as_csv,
    )

from pprl import pprl

def basic_test_pattern(
        patients_1 = None,
        patients_2 = None,
        expected_linkages = None,
        hashes_1 = "hashes1.csv",
        hashes_2 = "hashes2.csv",
        linkages = "linkages.csv",
        schema = "schema.json",
        secret = "secret.txt",
        threshold = 0.975,
        data_folder = None,
        schema_folder = None,
        ):

    test_dir = Path(__file__).parent

    if data_folder is None:
        data_folder = test_dir / "data"
    if schema_folder is None:
        schema_folder = test_dir / "schemas"

    with tempfile.TemporaryDirectory() as temp_dir:
        pprl._create_CLKs(
                data_folder = str(data_folder),
                patients = patients_1,
                schema = schema,
                schema_folder = str(schema_folder),
                secret = secret,
                output = hashes_1,
                output_folder = temp_dir,
                verbose=True
        )
        if patients_2 is None:
            hashes = [hashes_1]
        else:
            pprl._create_CLKs(
                    data_folder = str(data_folder),
                    patients = patients_2,
                    schema_folder = str(schema_folder),
                    schema = schema,
                    secret = secret,
                    output = hashes_2,
                    output_folder = temp_dir,
                    verbose=True
            )
            hashes = [hashes_1, hashes_2]

        expected_linkages_as_csv = ranges_as_csv(expected_linkages)

        pprl._match_CLKs(
                data_folder = temp_dir,
                hashes = hashes,
                threshold = threshold,
                output = linkages,
                output_folder = temp_dir,
                verbose=True)
        assert_file_contents(
                os.path.join(temp_dir, linkages),
                expected_linkages_as_csv
                )

def basic_error_pattern(error_type, **kwargs):
    with pytest.raises(error_type):
        basic_test_pattern(**kwargs)

