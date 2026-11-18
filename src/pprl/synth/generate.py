"""Run the whole synth pipeline: read a scenario, then write every output file.

scenario YAML --load_scenario--> Scenario
    Scenario --for each site--+--> SiteTables ---> <site>/OMOP/*.csv
        |                     +--> patient rows -> <site>/patients.csv
        +--> population.csv, true_linkages.csv, expected.csv
"""

from pathlib import Path

from pprl import _messages as msg
from pprl._inputs import check_output
from pprl._io import for_writing_folder, validated_name
from pprl._messages import Label
from pprl._ui import (
    ValidationContext,
    add_work,
    advance,
    finish_progress,
    ok_user,
    step,
)

from .csv_files import (
    patient_row,
    write_expected_csv,
    write_omop_csvs,
    write_patients_csv,
    write_population_csv,
    write_true_linkages_csv,
)
from .omop_tables import SiteTables
from .scenario import Scenario, load_scenario, output_folder_name
from .site_records import shuffled_rows, site_person_id, site_record, true_record
from .yaml_reading import ScenarioError


def synthesize(
    scenario: str | None = None,
    *,
    data_folder: Path | str,
    output_folder: Path | str,
) -> int:
    """Generate the synthetic dataset that a scenario describes.

    The dataset is written to ``<output_folder>/<scenario name>/``, or to
    ``<scenario name>_output/`` for a directory scenario. The folder must not
    exist yet.

    Parameters
    ----------
    scenario : str
        A scenario YAML file, or a directory of numbered YAML files, in
        data_folder.

    Returns
    -------
    int
        0 on success, 1 on validation failure.
    """
    ctx = ValidationContext()

    scenario_path = _ctx_scenario_path(ctx, scenario, data_folder)
    loaded = None
    if scenario_path is not None:
        with ctx.attempt(Label.SCENARIO_CONTENT, ScenarioError):
            loaded = load_scenario(scenario_path)

    output_path = None
    if scenario_path is not None:
        output_path = check_output(
            ctx,
            Label.OUTPUT_FOLDER,
            "output",
            output_folder_name(scenario_path),
            output_folder,
        )
    if ctx.conclude():
        return 1
    # Each of these is None only after a failure, which conclude has reported.
    if loaded is None:
        raise AssertionError
    if output_path is None:
        raise AssertionError

    with for_writing_folder(output_path) as staged_path:
        write_dataset(loaded, staged_path)
    finish_progress()
    ok_user(
        msg.WROTE_DATASET(
            output=output_path.name,
            individuals=len(loaded.individuals),
            sites=len(loaded.sites),
        )
    )
    return 0


def _ctx_scenario_path(
    ctx: ValidationContext, scenario: object, data_folder: Path | str
) -> Path | None:
    """Check that the scenario file or directory exists in data_folder."""
    with ctx.attempt(Label.SCENARIO, (TypeError, ValueError)) as attempt:
        validated_name(scenario, what="The scenario name")
    if attempt.failed:
        return None
    path = Path(data_folder) / str(scenario)  # A plain name, as checked above
    if not (path.is_file() or path.is_dir()):
        ctx.fail(Label.SCENARIO, msg.SCENARIO_NOT_FOUND(name=scenario))
        return None
    return path


# The steps of synth's progress bar, with each step's share of the bar.
STEPS = ((msg.STEP_GENERATING, 90), (msg.STEP_WRITING_SITES, 10))


def write_dataset(scenario: Scenario, output_dir: Path) -> None:
    """Write every output file for ``scenario`` into ``output_dir``.

    A multi-site scenario gets ``population.csv`` and ``true_linkages.csv``,
    plus one folder per site. A single-site scenario writes its site's files
    directly into ``output_dir``.
    """
    is_multi_site = len(scenario.sites) > 1
    output_dir.mkdir()
    if is_multi_site:
        person_ids = {
            (individual.individual_id, site): site_person_id(scenario, individual, site)
            for individual in scenario.individuals
            for site in individual.sites
        }
        for name, write in (
            ("population.csv", write_population_csv),
            ("true_linkages.csv", write_true_linkages_csv),
        ):
            write(output_dir / name, scenario.individuals, scenario.sites, person_ids)
    write_expected_csv(output_dir / "expected.csv", scenario.individuals)

    with_patients = "patients" in scenario.outputs
    with_pii = with_patients or scenario.person_locations
    add_work(len(scenario.individuals))
    truths = []
    for individual in scenario.individuals:
        truths.append(
            (individual, true_record(scenario, individual, with_pii=with_pii))
        )
        advance()
    # Each record counts twice: once as it is made, and once as its site's
    # files are written.
    step(
        msg.STEP_WRITING_SITES,
        2 * sum(len(individual.sites) for individual in scenario.individuals),
    )
    for site in scenario.sites:
        site_dir = output_dir / site if is_multi_site else output_dir
        records = []
        for individual, truth in truths:
            if site in individual.sites:
                records.append(site_record(scenario, individual, site, truth))
                advance()
        records.sort(key=lambda record: record.person_id)
        if "omop" in scenario.outputs:
            write_omop_csvs(
                site_dir / "OMOP", SiteTables(scenario, records, site).tables()
            )
        if with_patients:
            rows = [patient_row(record) for record in records]
            if scenario.pii_variation.shuffle_rows:
                rows = shuffled_rows(rows, scenario, site)
            write_patients_csv(site_dir / "patients.csv", rows)
        advance(len(records))
