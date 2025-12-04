import pandas as pd
import pytest
from pprl.pprl_utilities import validate_input_fields

class TestValidInput:
    """Direct unit tests for the validate_input_fields function."""

    def test_all_valid_records(self):
        """Test that all valid records pass validation."""
        df = pd.DataFrame({
            'row_id': [1, 2, 3],
            'first': ['Charles', 'Luna', 'Maria'],
            'last': ['White', 'Davis', 'Harris'],
            'city': ['Lakemont', 'Briarfield', 'Pine Hollow'],
            'zip': ['57431', '21748', '26592'],
            'dob': ['1959-08-24', '1966-01-12', '1965-09-30']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 3
        assert len(invalid) == 0
        assert list(valid['first']) == ['CHARLES', 'LUNA', 'MARIA']

    def test_string_capitalization_and_spaces(self):
        """Test that strings are capitalized and internal spaces preserved."""
        df = pd.DataFrame({
            'row_id': [1, 2],
            'first': ['charles', 'mary jane'],
            'last': ['white', 'van buren'],
            'city': ['lakemont', 'pine hollow'],
            'zip': ['57431', '21748'],
            'dob': ['1959-08-24', '1966-01-12']
        })

        valid, invalid = validate_input_fields(df)

        assert valid.iloc[0]['first'] == 'CHARLES'
        assert valid.iloc[1]['first'] == 'MARY JANE'
        assert valid.iloc[1]['last'] == 'VAN BUREN'
        assert valid.iloc[1]['city'] == 'PINE HOLLOW'

    def test_string_hyphens_preserved(self):
        """Test that hyphens are preserved in names and cities."""
        df = pd.DataFrame({
            'row_id': [1, 2],
            'first': ['Jean-Luc', 'Mary'],
            'last': ['Smith-Jones', 'Wilson'],
            'city': ['Wilkes-Barre', 'Winston-Salem'],
            'zip': ['18701', '27101'],
            'dob': ['1990-01-01', '1985-05-15']
        })

        valid, invalid = validate_input_fields(df)

        assert valid.iloc[0]['first'] == 'JEAN-LUC'
        assert valid.iloc[0]['last'] == 'SMITH-JONES'
        assert valid.iloc[0]['city'] == 'WILKES-BARRE'

    def test_string_apostrophes_preserved(self):
        """Test that apostrophes are preserved (after anyascii conversion)."""
        df = pd.DataFrame({
            'row_id': [1, 2],
            'first': ["D'Angelo", 'John'],
            'last': ["O'Brien", 'Smith'],
            'city': ["Coeur d'Alene", 'Boston'],
            'zip': ['83814', '02101'],
            'dob': ['1990-01-01', '1985-05-15']
        })

        valid, invalid = validate_input_fields(df)

        assert valid.iloc[0]['first'] == "D'ANGELO"
        assert valid.iloc[0]['last'] == "O'BRIEN"
        assert valid.iloc[0]['city'] == "COEUR D'ALENE"

    def test_string_periods_preserved(self):
        """Test that periods are preserved for abbreviations."""
        df = pd.DataFrame({
            'row_id': [1, 2],
            'first': ['J.R.', 'W.E.B.'],
            'last': ['Smith', 'DuBois'],
            'city': ['St. Louis', 'Mt. Vernon'],
            'zip': ['63101', '10550'],
            'dob': ['1990-01-01', '1985-05-15']
        })

        valid, invalid = validate_input_fields(df)

        assert valid.iloc[0]['first'] == 'J.R.'
        assert valid.iloc[0]['city'] == 'ST. LOUIS'
        assert valid.iloc[1]['city'] == 'MT. VERNON'

    def test_string_special_characters_removed(self):
        """Test that special characters are removed."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': ['John@#$'],
            'last': ['Doe!!!'],
            'city': ['Boston&*()'],
            'zip': ['02101'],
            'dob': ['1990-01-01']
        })

        valid, invalid = validate_input_fields(df)

        assert valid.iloc[0]['first'] == 'JOHN'
        assert valid.iloc[0]['last'] == 'DOE'
        assert valid.iloc[0]['city'] == 'BOSTON'

    def test_string_missing_values_invalid(self):
        """Test that various missing value representations are flagged."""
        df = pd.DataFrame({
            'row_id': [1, 2, 3, 4, 5],
            'first': ['', 'nan', 'None', 'nat', '999'],
            'last': ['Smith', 'Smith', 'Smith', 'Smith', 'Smith'],
            'city': ['Boston', 'Boston', 'Boston', 'Boston', 'Boston'],
            'zip': ['02101', '02101', '02101', '02101', '02101'],
            'dob': ['1990-01-01', '1990-01-01', '1990-01-01', '1990-01-01', '1990-01-01']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 1
        assert len(invalid) == 4

    def test_string_only_special_chars_invalid(self):
        """Test that strings with only special characters are invalid."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': ['@#$%^'],
            'last': ['Smith'],
            'city': ['Boston'],
            'zip': ['02101'],
            'dob': ['1990-01-01']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 0
        assert len(invalid) == 1
        assert 'first' in invalid.iloc[0]['invalid_cols']

    def test_zip_5_digit_valid(self):
        """Test that 5-digit zipcodes are valid."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': ['John'],
            'last': ['Doe'],
            'city': ['Boston'],
            'zip': ['02101'],
            'dob': ['1990-01-01']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 1
        assert valid.iloc[0]['zip'] == '02101'

    def test_zip_plus_4_converts_to_5(self):
        """Test that ZIP+4 is converted to 5-digit format."""
        df = pd.DataFrame({
            'row_id': [1, 2],
            'first': ['John', 'Jane'],
            'last': ['Doe', 'Smith'],
            'city': ['Boston', 'New York'],
            'zip': ['02101-1234', '10001-5678'],
            'dob': ['1990-01-01', '1985-05-15']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 2
        assert valid.iloc[0]['zip'] == '02101'
        assert valid.iloc[1]['zip'] == '10001'

    def test_zip_invalid_formats(self):
        """Test that invalid zipcode formats are flagged."""
        df = pd.DataFrame({
            'row_id': [1, 2, 3],
            'first': ['John', 'Jane', 'Bob'],
            'last': ['Doe', 'Smith', 'Jones'],
            'city': ['Boston', 'Boston', 'Boston'],
            'zip': ['ABCDE', '123', '71270354'],
            'dob': ['1990-01-01', '1990-01-01', '1990-01-01']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 0
        assert len(invalid) == 3

    def test_date_yyyy_mm_dd(self):
        """Test YYYY-MM-DD format."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': ['John'],
            'last': ['Doe'],
            'city': ['Boston'],
            'zip': ['02101'],
            'dob': ['1959-08-24']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 1
        assert valid.iloc[0]['dob'] == '1959-08-24'

    def test_date_yyyy_slash_mm_slash_dd(self):
        """Test YYYY/MM/DD format conversion."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': ['John'],
            'last': ['Doe'],
            'city': ['Boston'],
            'zip': ['02101'],
            'dob': ['1959/08/24']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 1
        assert valid.iloc[0]['dob'] == '1959-08-24'

    def test_date_mm_dd_yyyy(self):
        """Test MM-DD-YYYY format conversion."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': ['John'],
            'last': ['Doe'],
            'city': ['Boston'],
            'zip': ['02101'],
            'dob': ['08-24-1959']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 1
        assert valid.iloc[0]['dob'] == '1959-08-24'

    def test_date_mm_slash_dd_slash_yyyy(self):
        """Test MM/DD/YYYY format conversion."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': ['John'],
            'last': ['Doe'],
            'city': ['Boston'],
            'zip': ['02101'],
            'dob': ['08/24/1959']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 1
        assert valid.iloc[0]['dob'] == '1959-08-24'

    def test_date_mangled_excel(self):
        """Test MM/DD/YY format (cursed Excel dates)."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': ['John'],
            'last': ['Doe'],
            'city': ['Boston'],
            'zip': ['02101'],
            'dob': ['08/24/59']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 1
        assert valid.iloc[0]['dob'] == '1959-08-24'

    def test_date_missing_values_invalid(self):
        """Test that missing date values are flagged."""
        df = pd.DataFrame({
            'row_id': [1, 2, 3, 4],
            'first': ['John', 'Jane', 'Bob', 'Alice'],
            'last': ['Doe', 'Smith', 'Jones', 'Brown'],
            'city': ['Boston', 'Boston', 'Boston', 'Boston'],
            'zip': ['02101', '02101', '02101', '02101'],
            'dob': ['', 'nan', 'None', '999']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 0
        assert len(invalid) == 4

    def test_date_invalid_format(self):
        """Test that invalid date formats are flagged."""
        df = pd.DataFrame({
            'row_id': [1, 2],
            'first': ['John', 'Jane'],
            'last': ['Doe', 'Smith'],
            'city': ['Boston', 'Boston'],
            'zip': ['02101', '02101'],
            'dob': ['not-a-date', '13/45/2020']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 0
        assert len(invalid) == 2

    def test_multiple_invalid_fields(self):
        """Test records with multiple invalid fields."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': [''],
            'last': ['@@@'],
            'city': ['Boston'],
            'zip': ['ABCDE'],
            'dob': ['not-a-date']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 0
        assert len(invalid) == 1
        invalid_cols = invalid.iloc[0]['invalid_cols']
        assert 'first' in invalid_cols
        assert 'last' in invalid_cols
        assert 'zip' in invalid_cols
        assert 'dob' in invalid_cols

    def test_mixed_valid_invalid_separation(self):
        """Test that valid and invalid records are properly separated."""
        df = pd.DataFrame({
            'row_id': [1, 2, 3],
            'first': ['Charles', '', 'Maria'],
            'last': ['White', 'Davis', 'Harris'],
            'city': ['Lakemont', 'Briarfield', 'Pine Hollow'],
            'zip': ['57431', 'ABCDE', '26592'],
            'dob': ['1959-08-24', '1966-01-12', 'bad-date']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 1
        assert len(invalid) == 2
        assert valid.iloc[0]['row_id'] == 1

    def test_row_id_preserved_in_invalid(self):
        """Test that row_id values are preserved in invalid records."""
        df = pd.DataFrame({
            'row_id': [100, 200, 300],
            'first': ['', 'Jane', 'Bob'],
            'last': ['Doe', 'Smith', ''],
            'city': ['Boston', 'Boston', 'Boston'],
            'zip': ['02101', '02101', '02101'],
            'dob': ['1990-01-01', '1990-01-01', '1990-01-01']
        })

        valid, invalid = validate_input_fields(df)

        assert len(valid) == 1
        assert len(invalid) == 2
        assert 100 in invalid['row_id'].values
        assert 300 in invalid['row_id'].values

    def test_original_data_preserved_in_invalid(self):
        """Test that original uncleaned data is preserved in invalid records."""
        df = pd.DataFrame({
            'row_id': [1],
            'first': ['  john@#$  '],
            'last': ['  doe!!!  '],
            'city': ['  boston  '],
            'zip': ['INVALID'],
            'dob': ['bad-date']
        })

        valid, invalid = validate_input_fields(df)

        assert len(invalid) == 1
        assert invalid.iloc[0]['first'] == '  john@#$  '
        assert invalid.iloc[0]['last'] == '  doe!!!  '
        assert invalid.iloc[0]['zip'] == 'INVALID'
