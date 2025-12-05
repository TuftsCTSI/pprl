def assert_file_comparison(file_path_1, file_path_2):
    assert filecmp.cmp(file_path_1, file_path_2)

def assert_file_contents(file_path, expected):
    with open(file_path,'r') as file:
        observed = file.read()
        assert observed == expected

def ranges_as_csv(inputs):
    if inputs is None:
        return None 

    source_1, str_1, source_2, str_2 = inputs

    range_1 = collect_range_from_str(str_1) if str_1 != '' else []
    range_2 = collect_range_from_str(str_2) if str_2 != '' else []

    assert len(range_1) == len(range_2)

    return f'{source_1},{source_2}\n' + ''.join([f"{p},{q}\n" for p,q in zip(range_1,range_2)])

def collect_range_from_str(range_string):
    """
    For parsing a list of numbers as a set of ranges in general, see:
    https://gist.github.com/kgaughan/2491663

    Most of the solutions posted there assume the output is a set.
    In our case, we want to preserve the order, and we might have duplicates.

    Terms such as 3, 1-5, and 10-1 are supported.
    We can only count by 1, so 2,4,6,8,10 must be written out explicitly.
    All ranges are assumed to be inclusive.
    """
    out = []
    terms = [x.strip() for x in range_string.split(',')]
    for term in terms:
        if '-' in term:
            first, second = map(int, term.split('-'))
            if first <= second:
                collected_range = range(first, second + 1)
            else:
                collected_range = reversed(range(second, first + 1))
            out.extend(collected_range)
        else:
            value = int(term)
            out.append(value)

    print(out)

    return out
