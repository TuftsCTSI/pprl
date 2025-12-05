"""
Show a detailed results report for purpose-driven test sets.

Each test corresponds with a particular design aim, which is included as a docstring.
This and other relevant information is included along with test results.
"""
import pytest

import colorama
from colorama import Fore, Back, Style

import pytest

@pytest.hookspec()
def pytest_addoption(parser):
    parser.addoption(
        "--cmdopt",
        action="store",
        default="standard_pytest",
        help="standard_pytest or conformance_report",
        choices=("standard_pytest", "conformance_report"),
    )

def pytest_sessionstart(session):
    """(Conformance report only) Print a header line before running any tests"""
    if session.config.getoption("--cmdopt") == "conformance_report":
        colorama.init()

        print_test_header_line(
                "OBJECTIVE",
                "DESIGN SPECIFICATION",
                "TEST NAME",
                "RESULT",
                "(s)",
                )

def pytest_sessionfinish(session):
    """(Conformance report only) Final clean up"""
    if session.config.getoption("--cmdopt") == "conformance_report":
        colorama.deinit()

@pytest.hookimpl(tryfirst = True, hookwrapper = True)
def pytest_runtest_makereport(item, call):
    """(Conformance report only) Print a single line fully describing the test"""

    # Read values from the test funtion itself
    description = item.function.__doc__ or "<WARNING: This test is missing a docstring>"
    objective = item.parent.name.removeprefix("Test") or "<missing>"

    # Wait to read values from the results
    outcome = yield
    report = outcome.get_result()
    if report.outcome == "passed":
        result = Fore.GREEN + 'passed' + Style.RESET_ALL
    else:
        result = Style.BRIGHT + Fore.RED + 'FAILED' + Style.RESET_ALL

    duration = f"{report.duration:7.2f}"
    name = item.name.removeprefix("test_")

    if report.when == "call":
        if item.config.getoption("--cmdopt") == "conformance_report":
                print_test_report_line(
                        objective,
                        description,
                        Style.DIM + name + Style.RESET_ALL,
                        result,
                        duration,
                        )

#TODO: warn if a value is too long
def print_test_header_line(a,b,c,d,e):
    """Control the specific formatting for each line of the test report"""
    X, Y = Style.BRIGHT, Style.RESET_ALL
    print(f"{X}{a.rjust(11)}{Y} | {X}{b.ljust(90)}{Y} | {X}{c.ljust(37)}{Y} | {X}{d.rjust(6)}{Y} | {X}{e.rjust(8)}{Y}")

def print_test_report_line(a,b,c,d,e):
    """Control the specific formatting for each line of the test report"""
    print(f"{a.rjust(11)} | {b.ljust(90)} | {c.ljust(45)} | {d.rjust(6)} | {e.rjust(8)}")

