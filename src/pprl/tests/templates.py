import pytest
import tempfile
from pathlib import Path

import Levenshtein
from bitarray import bitarray
#from bitstring import ConstBitStream
from difflib import SequenceMatcher

from pprl.tests.utilities import (
        assert_file_comparison,
        assert_file_contents,
        ranges_as_csv,
        )

from pprl import pprl
from pprl.util import read_dataframe_from_CSV


from clkhash.serialization import deserialize_bitarray

def basic_test_pattern(
        capsys,
        patients_1 = None,
        patients_2 = None,
        expected_linkages = None,
        hashes_1 = "hashes1.csv",
        hashes_2 = "hashes2.csv",
        linkages = "linkages.csv",
        schema = "schema.json",
        secret = "secret.txt",
        secret_2 = None,
        threshold = 0.975,
        data_folder = None,
        schema_folder = None,
        ):

    test_dir = Path(__file__).parent

    if data_folder is None:
        data_folder = test_dir / "data"
    if schema_folder is None:
        schema_folder = test_dir / "schemas"
    if secret_2 is None:
        secret_2 = secret

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
                    secret = secret_2,
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
                Path(temp_dir) / linkages,
                expected_linkages_as_csv
                )

def basic_error_pattern(capsys, error_type, **kwargs):
    with pytest.raises(error_type):
        basic_test_pattern(capsys, **kwargs)

def compare_hashes(
        capsys,
        patients_1 = None,
        patients_2 = None,
        hashes_1 = "hashes1.csv",
        hashes_2 = "hashes2.csv",
        schema = "schema.json",
        secret = "secret.txt",
        secret_2 = None,
        data_folder = None,
        schema_folder = None,
        lower_bound = 0.0,
        upper_bound = 1.0,
        ):

    test_dir = Path(__file__).parent

    if data_folder is None:
        data_folder = test_dir / "data"
    if schema_folder is None:
        schema_folder = test_dir / "schemas"
    if secret_2 is None:
        secret_2 = secret

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
        pprl._create_CLKs(
                data_folder = str(data_folder),
                patients = patients_2,
                schema_folder = str(schema_folder),
                schema = schema,
                secret = secret_2,
                output = hashes_2,
                output_folder = temp_dir,
                verbose=True
                )

        def read_hashes_as_single_bitstring(file_name):
            df = read_dataframe_from_CSV(Path(temp_dir) / file_name)

            joined_hashes = bitarray()
            for clk in df['clk']:
                joined_hashes += deserialize_bitarray(clk)

            return joined_hashes

        hash_str_1 = read_hashes_as_single_bitstring(hashes_1)
        hash_str_2 = read_hashes_as_single_bitstring(hashes_2)

        levenshtein_distance = Levenshtein.distance(
                hash_str_1,
                hash_str_2,
                )

        total_bits = len(hash_str_1)
        
        similarity = 1 - levenshtein_distance / total_bits

        print(f"Total bits: {total_bits}")
        print(f"Levenshtein distance: {levenshtein_distance}")
        print(f"Similarity: {similarity}")
        print(f"(Should be between {lower_bound} and {upper_bound})")

        assert lower_bound <= similarity <= upper_bound

