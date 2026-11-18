"""Benchmark how long each command takes, and its peak memory, by records per site.

These benchmarks are not part of the test suite, because they take minutes.
Run them with `make bench`, which saves the results and shows them as a
table. Each step runs once, as a user runs it.
"""

from typing import TYPE_CHECKING

import pytest

from tests.benchmarks.project import STEPS, Project

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_benchmark.fixture import BenchmarkFixture

pytestmark = pytest.mark.performance

SIZES = (1_000, 10_000, 100_000)
CASES = [(step, size) for step in STEPS for size in SIZES]


@pytest.fixture(scope="session")
def projects(tmp_path_factory: pytest.TempPathFactory) -> Callable[[int], Project]:
    """Make the project for each size the first time it is needed.

    The project is kept for every step at that size.
    """
    made = {}

    def project(size: int) -> Project:
        if size not in made:
            made[size] = Project(tmp_path_factory.mktemp(f"records_{size}"), size)
        return made[size]

    return project


@pytest.mark.parametrize(
    ("step", "size"), CASES, ids=[f"{step}-{size}" for step, size in CASES]
)
def test_step(
    benchmark: BenchmarkFixture,
    projects: Callable[[int], Project],
    step: str,
    size: int,
) -> None:
    """Measure how long a command takes from start to finish, and its peak memory.

    The command runs with ``size`` records per site.
    """
    project = projects(size)
    project.prepare(step)  # Run the earlier steps, untimed.
    measure = benchmark.pedantic(project.run, args=(step,), rounds=1, iterations=1)
    benchmark.extra_info.update(step=step, size=size, memory=measure.memory)
