import os
import yaml

def read_config_file(config, allowed_config_names):
    """
    Parse a YAML config file and validate the returned dictionary
    """
    configuration = yaml.safe_load(open(config))
    observed_config_names = set(configuration.keys())
    unexpected_config_names = observed_config_names - allowed_config_names
    if bool(unexpected_config_names):
        print("The following variables were not expected in the configuration file:")
        print(unexpected_config_names)
        print(" Only the followin variables should be used:")
        print(allowed_config_names)

    #TODO: test to avoid mixing hashing schema with linking schema?

    unused_config_names = allowed_config_names - observed_config_names 
    if bool(unexpected_config_names):
        print("The following variables weren't set in the config file:")
        print(unused_config_names)
        print("Default values will be asigned instead.")

    #configuration.setdefault('schema', 'schema.json')
    #configuration.setdefault('secret', 'secret.txt')
    #configuration.setdefault('output', 'out.csv')
    #configuration.setdefault('quiet', True)
    #configuration.setdefault('data_folder', os.path.join(os.getcwd(), "my_files"))
    #configuration.setdefault('schema_folder', os.path.join(os.getcwd(), "schemas"))


#TODO: warn a user if any unexpected names appear in the dictionary!
#TODO: warn a user if a default value is used

    return configuration

def validated_file_path(descriptor, file_name, file_directory):
    """
    Assmeble, validate, and return a file path
    """
    if file_name is None:
        raise TypeError(f'The name of a {descriptor} file must be provided.')
    patient_file_path = os.path.join(file_directory, file_name)
    if not os.path.isfile(patient_file_path):
        raise FileNotFoundError(f'Cannot find {descriptor} file: {patient_file_path}') 
    return patient_file_path

