"""
Show a detailed results report for purpose-driven test sets.

Each test corresponds with a particular design aim, which is included as a docstring.
This and other relevant information is included along with test results.
"""
import pytest

def pytest_sessionstart(session):
    """Print a header line before running any tests"""
    print_test_report_line(
            "OBJECTIVE",
            "TEST",
            "SCOPE",
            "RESULT",
            "TIME (s)",
            )

@pytest.hookimpl(tryfirst = True, hookwrapper = True)
def pytest_runtest_makereport(item, call):
    """Print a single line fully describing the test"""

    # Read values from the test funtion itself
    description = item.function.__doc__ or "<WARNING: This test is missing a docstring>"
    objective = item.parent.name.removeprefix("Test") or "<missing>"

    # Wait to read values from the results
    outcome = yield
    report = outcome.get_result()
    # TODO: use ✅ or ❌
    #result = "\U00002705" if report.outcome == "passed" else "\U0001F600",
    # Might be worth using a separate library
    result = report.outcome
    duration = f"{report.duration:7.2f}"
    name = item.name.removeprefix("test_")

    if report.when == "call":
        print_test_report_line(
                objective,
                name,
                description,
                result,
                duration,
                )

#TODO: warn if a value is too long
def print_test_report_line(a,b,c,d,e):
    """Control the specific formatting for each line of the test report"""
    print(f"{a.rjust(11)} | {b.ljust(30)} | {c.ljust(90)} | {d.rjust(6)} | {e.rjust(8)}")

