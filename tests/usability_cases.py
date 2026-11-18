"""Define the usability cases: one input given to one command, and what pprl does.

Each Case runs through the CLI, and checks the
exit code and the text the user sees. The cases are grouped by command. The
usability grid lists the kinds of input (``input``), and checks that every
command has a case, or a reason not to, for each kind.

A case states what pprl does today. If that is not what pprl should do, the
case explains why in ``problem``. Fixing the code will then make the case
fail, and the case should be updated to the new behavior, without its
``problem``.
"""

import io
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pandas as pd
import yaml

from pprl import _bundle, _pprl_file, _wording
from pprl import _messages as msg
from pprl._io import read_strings_csv, read_text, write_text
from pprl._shared import Member
from tests.helpers import (
    MERGE_TABLES,
    PROJECT_SETTINGS,
    SITE_TABLES,
    SITES,
    TEST_PROJECT,
    TEST_SET,
    THRESHOLD,
    provenance,
    write_selected,
)
from tests.helpers import SYNTH_FIELDS as FIELDS
from tests.usability_grid import (
    CONFIG_NAMES,
    DEFAULT_CONFIGS,
    DOC_PATH,
    DROP,
    Case,
    edited,
    render_markdown,
    repacked,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from pprl._wording import Message

PATIENTS_HEADER = "person_id,name,city,state,zip,dob\n"
CLEANED_HEADER = "study_id,name,city,state,zip,dob\n"


def patients(*rows: str) -> str:
    """Make patient file text, as a site exports it (keyed by person_id)."""
    return PATIENTS_HEADER + "".join(f"{row}\n" for row in rows)


def cleaned_patients(*rows: str) -> str:
    """Make patient file text, as pprl clean writes it (keyed by study_id)."""
    return CLEANED_HEADER + "".join(f"{row}\n" for row in rows)


JOHN = "1,John Example,Boston,MA,02101,1990-01-01"
JANE = "2,Jane Surname,Salem,MA,01970,1985-06-15"
JANE_AS_1 = JANE.replace("2,", "1,", 1)
MALFORMED_CSV = 'person_id,name\n1,Jo,extra,fields\n2,"unclosed\n'
OUTSIDE = "{workspace}/../outside"  # A folder next to the workspace


def with_column(name: str) -> Callable[[Path], str]:
    """Add an extra column to a workspace file."""
    return edited(name, lambda df: df.assign(ward="7"))


def two_sites(name: str) -> Callable[[Path], str]:
    """Give a hash file's last row another site."""
    return edited(
        name, lambda df: df.assign(site=[df["site"][0]] * (len(df) - 1) + ["site_c"])
    )


# ---------------------------------------------------------------------------
# Config files, which mostly behave alike across commands
# ---------------------------------------------------------------------------


_READ_ALIKE = ("empty", "invalid", "not-mapping")


def config_cases(command: str, **expect: dict | None) -> list[Case]:
    """Make the config-file cases for a command.

    ``expect`` maps a case name below to the Case arguments (says, accepted,
    crashes, problem) to use instead of those for commands that read a
    config file. A value of None leaves that case out.
    """
    usual: dict[str, dict[str, Any]] = {
        "missing": {"says": (msg.CONFIG_NOT_FOUND, "missing.yml")},
        "empty": {"says": msg.CONFIG_EMPTY},
        "invalid": {"says": msg.CONFIG_NOT_YAML},
        "not-mapping": {"says": msg.CONFIG_NOT_MAPPING},
        "outside": {"says": (msg.NAME_OUTSIDE, "The config file name")},
        "absolute": {"says": (msg.NAME_OUTSIDE, "The config file name")},
        "unknown-key": {"says": (msg.CONFIG_UNKNOWN_KEYS, "colour")},
        "reserved-key": {"says": (msg.CONFIG_RESERVED_KEYS, "data_folder")},
    }
    inputs: dict[str, tuple[str, str, dict[str, Any]]] = {
        "missing": (
            "config-missing",
            "named a config file that is not there",
            {"argv": (command, "missing.yml")},
        ),
        "empty": ("config-empty", "gave an empty config file", {"config_text": ""}),
        "invalid": (
            "config-invalid",
            "gave a config file that is not YAML",
            {"config_text": ": : : not valid yaml ["},
        ),
        "not-mapping": (
            "config-not-mapping",
            "gave a list instead of key: value lines",
            {"config_text": "- patients.csv\n- hashes.csv\n"},
        ),
        "outside": (
            "config-outside",
            "named a config file outside the workspace (../outside.yml)",
            {
                "files": {"../outside.yml": yaml.dump(DEFAULT_CONFIGS[command])},
                "argv": (command, "../outside.yml"),
            },
        ),
        "absolute": (
            "config-outside",
            "named the config file by its absolute path",
            {"argv": (command, "{workspace}/" + CONFIG_NAMES[command])},
        ),
        "unknown-key": (
            "unknown-key",
            "added a key the command does not know (colour)",
            {"config": {"colour": "teal"}},
        ),
        "reserved-key": (
            "reserved-key",
            "set an internal key (data_folder)",
            {"config": {"data_folder": "elsewhere"}},
        ),
    }
    return [
        Case(command, input_key, did, **setup, **(expect.get(name, usual[name]) or {}))
        for name, (input_key, did, setup) in inputs.items()
        if expect.get(name, usual[name]) is not None
        # Every config file is read the same way, so these run only on bundles
        # and synth.
        and not (command in ("guides", "sweep") and name in _READ_ALIKE)
    ]


# ---------------------------------------------------------------------------
# pprl, bundles
# ---------------------------------------------------------------------------

PPRL = [
    Case(
        "pprl",
        "no-command",
        "ran pprl alone",
        msg.HELP_FOOTER,
        argv=(),
    ),
    Case(
        "pprl",
        "unknown-command",
        "ran pprl nonexistent",
        (msg.UNKNOWN_COMMAND, "nonexistent"),
        argv=("nonexistent",),
    ),
    Case(
        "pprl",
        "version",
        "ran pprl --version",
        f"pprl {version('pprl')}",
        argv=("--version",),
        accepted=True,
    ),
    Case(
        "pprl",
        "valid",
        "ran pprl --help, in a workspace (every command)",
        msg.HELP_GROUPS[-1][0],
        argv=("--help",),
        accepted=True,
    ),
    Case(
        "pprl",
        "valid",
        "ran pprl hash --help, in a workspace (its files, by their names there)",
        "outbox/linkage/site_a.hashes.pprl, for the linkage agent",
        argv=("hash", "--help"),
        accepted=True,
    ),
]


BUNDLES_WROTE = "Wrote outbox/site_a/site_a.pprl, outbox/site_b/site_b.pprl"


def _scores_rules(**selection: object) -> dict:
    """Make `tables` for project.yml: the scores table, with ``selection``.

    ``selection`` gives the table's `keep` and `censor`.
    """
    return {"scores": MERGE_TABLES["scores"] | selection}


BUNDLES = [
    *config_cases("bundles", missing=None, outside=None, absolute=None),
    Case(
        "bundles",
        "config-missing",
        "has no project.yml",
        (msg.CONFIG_NOT_FOUND, "project.yml"),
        config_text=DROP,
    ),
    *(
        Case("bundles", "missing-key", f"left out {what}", says, config={key: DROP})
        for key, what, says in (
            ("name", "the project's name", (msg.PROJECT_NAME_INVALID, "is missing")),
            ("id", "the id column", msg.ID_COLUMN_MISSING),
            ("sites", "sites", (msg.SITES_NOT_LIST, "is missing")),
            ("fields", "fields", msg.FIELDS_MISSING),
            ("hash_length", "the hash length", msg.HASH_LENGTH_INVALID),
            ("fill_ratio", "the fill ratio", msg.FILL_RATIO_INVALID),
            ("threshold", "threshold", msg.THRESHOLD_INVALID),
            ("secret_check", "the same-secret check", msg.SECRET_CHECK_INVALID),
            ("tables", "the merge rules", msg.TABLES_NOT_SETTINGS),
        )
    ),
    Case(
        "bundles",
        "wrong-type",
        "gave each site with a patient file, instead of a list of sites",
        msg.SITES_NOT_LIST,
        config={"sites": {"site_a": "patients_a.csv", "site_b": "patients_b.csv"}},
    ),
    Case(
        "bundles",
        "wrong-type",
        "gave a site name with a space",
        (msg.INVALID_SITE, '"site a"'),
        config={"sites": ["site a", "site_b"]},
    ),
    Case(
        "bundles",
        "out-of-range",
        "gave a threshold above 1",
        msg.THRESHOLD_INVALID,
        config={"threshold": 1.5},
    ),
    Case(
        "bundles",
        "wrong-type",
        "gave the same-secret check as text, instead of true or false",
        (msg.SECRET_CHECK_INVALID, "but is no."),
        config={"secret_check": "no"},
    ),
    Case(
        "bundles",
        "out-of-range",
        "gave a hash length that is not a whole number of bytes",
        (msg.HASH_LENGTH_INVALID, "is 1020"),
        config={"hash_length": 1020},
    ),
    Case(
        "bundles",
        "out-of-range",
        "gave a hash length too short for its fields",
        (msg.HASH_LENGTH_INVALID, "is 32"),
        config={"hash_length": 32},
    ),
    Case(
        "bundles",
        "out-of-range",
        "gave a fill ratio of 0",
        (msg.FILL_RATIO_INVALID, "is 0"),
        config={"fill_ratio": 0},
    ),
    Case(
        "bundles",
        "wrong-type",
        "gave the fill ratio as text",
        (msg.FILL_RATIO_INVALID, "is full"),
        config={"fill_ratio": "full"},
    ),
    Case(
        "bundles",
        "out-of-range",
        "asked for a negative number of XOR folds",
        msg.XOR_FOLDS_INVALID,
        config={"xor_folds": -1},
    ),
    Case(
        "bundles",
        "wrong-type",
        "gave a project salt that is not text",
        msg.PROJECT_SALT_INVALID,
        config={"project_salt": 1234},
    ),
    Case(
        "bundles",
        "out-of-range",
        "gave a record salt of no length",
        msg.RECORD_SALT_INVALID,
        config={"record_salt": {"field": "zip", "length": 0}},
    ),
    Case(
        "bundles",
        "out-of-range",
        "gave a record salt of a column that is not hashed",
        msg.RECORD_SALT_NOT_A_FIELD,
        config={"record_salt": {"field": "shoe_size"}},
    ),
    *(
        Case("bundles", input_key, did, says, config=config)
        for input_key, did, says, config in (
            (
                "wrong-type",
                "gave the fields as a file name",
                msg.FIELDS_NOT_SETTINGS,
                {"fields": "fields.json"},
            ),
            (
                "wrong-type",
                "gave empty fields",
                (msg.FIELDS_NOT_SETTINGS, "but is empty"),
                {"fields": {}},
            ),
            (
                "wrong-type",
                "gave a field without its weight",
                (msg.FIELD_NO_WEIGHT, "The field dob"),
                {"fields": FIELDS | {"dob": {"type": "date"}}},
            ),
            (
                "wrong-type",
                "gave a field's weight as a word",
                (msg.FIELD_BAD_WEIGHT, "The weight of the field name"),
                {"fields": FIELDS | {"name": "high"}},
            ),
            (
                "out-of-range",
                "gave a field a weight of 0",
                (msg.FIELD_BAD_WEIGHT, "The weight of the field city"),
                {"fields": FIELDS | {"city": 0}},
            ),
            (
                "unknown-key",
                "gave a field its bits, as an earlier release did",
                (msg.FIELD_UNKNOWN_KEYS, "The field dob"),
                {"fields": FIELDS | {"dob": {"bits": 300, "type": "date"}}},
            ),
            (
                "unknown-choice",
                "gave a field a type that does not exist",
                (msg.FIELD_BAD_TYPE, "The field dob"),
                {"fields": FIELDS | {"dob": {"weight": 300, "type": "day"}}},
            ),
            (
                "wrong-type",
                "gave the id as a list of columns",
                msg.ID_COLUMN_INVALID,
                {"id": ["person_id", "visit_id"]},
            ),
            (
                "project",
                "named a field as the id column",
                (msg.ID_COLUMN_IS_FIELD, "`id` is name"),
                {"id": "name"},
            ),
            (
                "project",
                "named pprl's own study_id as the id column",
                (msg.ID_COLUMN_RESERVED, "`id` is study_id"),
                {"id": "study_id"},
            ),
            (
                "project",
                "gave a table a primary key without the id column",
                (msg.PRIMARY_KEY_INVALID, "includes person_id"),
                {"tables": {"scores": {"primary_key": ["score"]}}},
            ),
            (
                "project",
                "gave a rule a column the table does not have",
                (msg.RULE_INVALID, "names ward, which is not a column of scores"),
                {"tables": _scores_rules(keep=[{"not_empty": "ward"}])},
            ),
            (
                "unknown-choice",
                "gave a rule that does not exist (not_null)",
                (msg.RULE_INVALID, "should be one of `not_empty`"),
                {"tables": _scores_rules(keep=[{"not_null": "score"}])},
            ),
            (
                "wrong-type",
                "listed a rule's values as numbers, not in quotes",
                (msg.RULE_INVALID, "each in quotes"),
                {"tables": _scores_rules(keep=[{"one_of": {"score": [5, 7]}}])},
            ),
            (
                "wrong-type",
                "gave keep as one rule, not a list",
                (msg.KEEP_INVALID, "should be a list of rules"),
                {"tables": _scores_rules(keep={"not_empty": "score"})},
            ),
            (
                "project",
                "matched a found_in rule without the id column",
                (msg.RULE_INVALID, "person_id among them"),
                {
                    "tables": _scores_rules(
                        keep=[{"found_in": {"table": "visits", "on": ["score"]}}]
                    )
                    | {"visits": {"primary_key": ["person_id"]}}
                },
            ),
            (
                "project",
                "looked in a table the project does not have",
                (msg.RULE_INVALID, "looks in visits"),
                {
                    "tables": _scores_rules(
                        keep=[{"found_in": {"table": "visits", "on": ["person_id"]}}]
                    )
                },
            ),
            (
                "project",
                "made two tables' found_in rules look in each other",
                (msg.RULES_CIRCULAR, "scores, visits"),
                {
                    "tables": _scores_rules(
                        keep=[{"found_in": {"table": "visits", "on": ["person_id"]}}]
                    )
                    | {
                        "visits": {
                            "primary_key": ["person_id"],
                            "keep": [
                                {"found_in": {"table": "scores", "on": ["person_id"]}}
                            ],
                        }
                    }
                },
            ),
            (
                "project",
                "censored a column of the primary key",
                (msg.CENSOR_INVALID, "none in its `primary_key`"),
                {"tables": _scores_rules(censor=["person_id"])},
            ),
        )
    ),
    Case(
        "bundles",
        "every-choice",
        "gave every kind of rule, and a censored column",
        BUNDLES_WROTE,
        config={
            "tables": _scores_rules(
                keep=[
                    {"not_empty": "score"},
                    {"one_of": {"score": ["5", "7"]}},
                    {"found_in": {"table": "visits", "on": ["person_id"]}},
                ],
            )
            | {
                "visits": {
                    "primary_key": ["person_id"],
                    "column": {"ward": {"rank_by": []}},
                    "censor": ["ward"],
                }
            }
        },
        accepted=True,
    ),
    Case(
        "bundles",
        "every-choice",
        "hashed dob as text instead of a date",
        BUNDLES_WROTE,
        config={"fields": FIELDS | {"dob": {"weight": 300, "type": "text"}}},
        accepted=True,
    ),
    Case(
        "bundles",
        "too-few",
        "listed one site",
        msg.SITES_NOT_LIST,
        config={"sites": ["site_a"]},
    ),
    Case(
        "bundles",
        "project",
        "listed a site twice",
        (msg.SITES_REPEATED, "site_a"),
        config={"sites": ["site_a", "site_b", "site_a"]},
    ),
    Case(
        "bundles",
        "output-exists",
        "already has outbox/site_a/site_a.pprl",
        (msg.BUNDLE_EXISTS, "outbox/site_a/site_a.pprl"),
        files={"outbox/site_a/site_a.pprl": "an earlier bundle\n"},
    ),
    Case(
        "bundles",
        "valid",
        "made a project with two sites",
        (BUNDLES_WROTE, "outbox/linkage/linkage.pprl, outbox/merge/merge.pprl."),
        accepted=True,
        creates="outbox/merge/merge.pprl",
    ),
]

# ---------------------------------------------------------------------------
# synth
# ---------------------------------------------------------------------------

SYNTHESIZED = "Wrote the dataset to the folder synth"


def _histories(**overrides: object) -> dict:
    """Give one individual of a scenario a generated history."""
    groups = [{"sites": ["site_a"], "count": 1, "modules": {"hypertension": {}}}]
    return {"generated_individuals": groups, "measurement_years": [2023, 2024]} | (
        overrides
    )


SYNTH = [
    *config_cases(
        "synth",
        missing=None,
        outside=None,
        absolute=None,
        empty={"says": (msg.SCENARIO_MISSING, "missing `seed`")},
        invalid={"says": (msg.SCENARIO_NOT_YAML, "synth.yml")},
        **{
            "not-mapping": {"says": (msg.SCENARIO_NOT_MAPPING, "synth.yml")},
            "unknown-key": None,
            "reserved-key": None,
        },
    ),
    Case(
        "synth",
        "config-missing",
        "has no synth.yml",
        (msg.SCENARIO_NOT_FOUND, "synth.yml"),
        config_text=DROP,
    ),
    Case(
        "synth",
        "missing-key",
        "left out outputs",
        (msg.SCENARIO_MISSING, "missing `outputs`"),
        config={"outputs": DROP},
    ),
    Case(
        "synth",
        "wrong-type",
        "gave seed as a list",
        (msg.SCENARIO_BAD_SEED, "seed:"),
        config={"seed": [1, 2]},
    ),
    Case(
        "synth",
        "wrong-type",
        "gave a group count as text",
        (msg.SCENARIO_WRONG_TYPE, "generated_individuals[1].count"),
        config={"generated_individuals": [{"sites": ["site_a"], "count": "ten"}]},
    ),
    Case(
        "synth",
        "out-of-range",
        "gave a chance above 1",
        (msg.SCENARIO_RATE, "pii.name.missing"),
        config={"pii": {"name": {"missing": 1.5}}},
    ),
    Case(
        "synth",
        "unknown-choice",
        "asked for an output that does not exist (pii)",
        (msg.SCENARIO_UNKNOWN_OUTPUT, "outputs:"),
        config={"outputs": ["pii"]},
    ),
    *(
        Case(
            "synth",
            "every-choice",
            f"asked for outputs: {', '.join(outputs)}",
            SYNTHESIZED,
            config={"outputs": outputs},
            accepted=True,
        )
        for outputs in (["omop"], ["patients"], ["omop", "patients"])
    ),
    Case(
        "synth",
        "output-exists",
        "already has a synth/ folder",
        (msg.OUTPUT_EXISTS, "synth"),
        files={"synth/notes.txt": "earlier output\n"},
    ),
    *(
        Case("synth", "scenario", did, says, config=config)
        for did, says, config in (
            (
                "added an unknown key",
                (msg.SCENARIO_UNKNOWN_KEY, "favourite_colour:"),
                {"favourite_colour": "teal"},
            ),
            (
                "used a full OMOP table name",
                (msg.SCENARIO_SHORT_TABLE_NAME, "write `condition`"),
                {"condition_occurrence": []},
            ),
            (
                "used a full OMOP column name",
                (msg.SCENARIO_SHORT_COLUMN_NAME, "individuals[1].person_source_value"),
                {"individuals": [{"source_value": "a", "person_source_value": "b"}]},
            ),
            (
                "named a site the scenario does not have",
                (msg.SCENARIO_UNKNOWN_SITE, "generated_individuals[1].sites"),
                {"generated_individuals": [{"sites": ["site_z"], "count": 1}]},
            ),
            (
                "gave a site name with spaces",
                (msg.SCENARIO_INVALID_SITE, "sites[1]"),
                {"sites": ["has spaces"]},
            ),
            (
                "set a key under at_sites that is not PII",
                (msg.SCENARIO_NOT_PII_OR_PERSON, "at_sites.site_b.nickname"),
                {
                    "individuals": [
                        {
                            "source_value": "a",
                            "at_sites": {"site_b": {"nickname": "Jo"}},
                        }
                    ]
                },
            ),
            (
                "set a date of birth under at_sites",
                (msg.SCENARIO_FIXED_PERSON_COLUMN, "at_sites.site_b.year_of_birth"),
                {
                    "individuals": [
                        {
                            "source_value": "a",
                            "at_sites": {"site_b": {"year_of_birth": 1990}},
                        }
                    ]
                },
            ),
            (
                "named an unknown history module",
                (msg.SCENARIO_UNKNOWN_MODULE, "modules.flu"),
                _histories(
                    generated_individuals=[
                        {"sites": ["site_a"], "count": 1, "modules": {"flu": {}}}
                    ]
                ),
            ),
            (
                "used history modules without measurement_years",
                (msg.SCENARIO_MISSING, "missing `measurement_years`"),
                _histories(measurement_years=DROP),
            ),
            (
                "gave measurement_years backwards",
                (msg.SCENARIO_YEAR_SPAN, "measurement_years:"),
                _histories(measurement_years=[2025, 2023]),
            ),
            (
                "gave event_sharing chances that do not add up to 1",
                (msg.SCENARIO_SHARING_TOTAL, "not 0.9"),
                {"event_sharing": {"one_site": 0.5, "all_sites": 0.4}},
            ),
            (
                "gave relatives chances that add up to over 1",
                (msg.SCENARIO_RELATIVES_TOTAL, "up to 1.5"),
                {"relatives": {"family": 1, "twin": 0.5}},
            ),
            (
                "gave site_coverage for an unknown site",
                (msg.SCENARIO_UNKNOWN_SITE, "site_coverage.site_z"),
                {"site_coverage": {"site_z": ["hypertension"]}},
            ),
            (
                "gave an unknown person_ids choice",
                msg.SCENARIO_PERSON_IDS,
                {"person_ids": "random"},
            ),
            (
                "listed an unknown site for a care site",
                (msg.SCENARIO_UNKNOWN_SITE, "care_site[1].sites"),
                {"care_site": [{"source_value": "chc", "sites": ["site_z"]}]},
            ),
        )
    ),
    Case(
        "synth",
        "valid",
        "ran a two-site scenario",
        SYNTHESIZED,
        accepted=True,
        creates="synth/site_b/patients.csv",
    ),
]

# ---------------------------------------------------------------------------
# The bundle, for every pprl command in a workspace
# ---------------------------------------------------------------------------

# Each pprl command's bundle, and a command that another role runs.
_OTHER_ROLE = {"link": "site", "merge": "linkage"}


def _resaved(path: Path) -> bytes:
    """Resave a bundle's bytes, as an editor would.

    The editor uses Windows line endings, and changes a byte it can't show.
    """
    changed = path.read_bytes().replace(b"\n", b"\r\n")
    return changed[:-1] + bytes([changed[-1] ^ 1])


def _replaced(path: Path) -> bytes:
    """Rewrite a bundle's bytes, as a later run of pprl bundles would.

    The settings are the same, but in a different bundle set.
    """
    staged = path.with_name("replaced.tmp")
    _bundle.write(staged, _bundle.read(path) | {"set": OTHER_SET})
    data = staged.read_bytes()
    staged.unlink()
    return data


def bundle_cases() -> list[Case]:
    """Make the cases for the bundle that a command reads its settings from.

    Every command reads the bundle the same way, so these run only on hash.
    """
    command, name = "hash", "site_a.pprl"
    return [
        Case(
            command,
            "bundle-missing",
            "ran it in a workspace without its bundle",
            (msg.BUNDLE_MISSING, name),
            bundle_text=DROP,
        ),
        Case(
            command,
            "bundle-changed",
            "opened and saved the bundle in an editor",
            (msg.BUNDLE_UNUSABLE, msg.BUNDLE_REASONS["not a bundle"]()),
            bundle_text=_resaved,
        ),
        Case(
            command,
            "bundle-changed",
            "put in a bundle from a later set (pprl bundles run again)",
            (msg.BUNDLE_UNUSABLE, msg.BUNDLE_REASONS["replaced"]()),
            bundle_text=_replaced,
        ),
        Case(
            command,
            "bundle-other",
            "put another project's bundle in the workspace",
            (msg.BUNDLE_UNUSABLE, msg.BUNDLE_REASONS["other workspace"]()),
            bundle={"project": {"id": OTHER_PROJECT, "name": "Other"}},
        ),
    ]


def setting_case(command: str) -> Case:
    """Make the case for a command whose bundle lacks a setting it needs."""
    role = _OTHER_ROLE.get(command, "linkage")
    return Case(
        command,
        "setting-missing",
        f"ran it in {msg.owner(role)}'s workspace",
        (msg.SETTING_MISSING, f"pprl {command} needs the setting"),
        role=role,
    )


OTHER_PROJECT = "11111111-1111-4111-8111-111111111111"
OTHER_SET = "22222222-2222-4222-8222-222222222222"

# ---------------------------------------------------------------------------
# pprl files, for every command that reads one
# ---------------------------------------------------------------------------


def _bundle_bytes(folder: Path) -> bytes:
    """Read the bytes of the workspace's own bundle, or of a site's bundle.

    The coordinator's workspace has no bundle, so it uses a site's.
    """
    bundles = [p for p in Path(folder).glob("*.pprl") if _bundle.is_bundle_name(p.name)]
    if bundles:
        return bundles[0].read_bytes()
    staged = Path(folder) / ".staged.pprl"
    _bundle.write(
        staged,
        {
            "project": {"id": TEST_PROJECT, "name": PROJECT_SETTINGS["name"]},
            "set": TEST_SET,
            "role": "linkage",
            "sites": list(SITES),
            "threshold": THRESHOLD,
            "secret_check": True,
        },
    )
    data = staged.read_bytes()
    staged.unlink()
    return data


def _resaved_file(name: str) -> Callable[[Path], bytes]:
    """Make file bytes for a pprl file resaved by an editor, with a byte changed.

    The workspace's pprl file ``name`` is changed as an editor would leave
    it when saving with Windows line endings, and one byte is changed, as a
    spreadsheet would change it.
    """

    def data(folder: Path) -> bytes:
        original = (Path(folder) / name).read_bytes()
        changed = original.replace(b"\n", b"\r\n")
        return changed[:-1] + bytes([changed[-1] ^ 1])

    return data


def pprl_cases(
    command: str,
    name: str,
    *,
    skip: Iterable[str] = (),
    **expect: str | Message | tuple,
) -> list[Case]:
    """Make the cases for a pprl file ``name`` that a command reads.

    There is one case per kind of pprl file mistake, with the message it
    gets. ``expect`` replaces the usual message for a case, and ``skip``
    leaves cases out.
    """
    usual = {
        "not-pprl": (
            "gave a CSV file where its pprl file belongs",
            msg.NOT_A_PPRL_FILE,
            "study_id,hash\n1,AA==\n",
        ),
        "pprl-damaged": (
            "gave a pprl file opened and saved in a spreadsheet",
            msg.FILE_DAMAGED,
            _resaved_file(name),
        ),
        "pprl-other-kind": (
            "gave another kind of pprl file under its name",
            msg.FILE_OTHER_KIND,
            repacked(name, kind="bundle" if name.endswith("tables.pprl") else "tables"),
        ),
        "pprl-bundle": (
            "gave a bundle under the file's name",
            msg.FILE_IS_BUNDLE,
            _bundle_bytes,
        ),
        "pprl-other-project": (
            "gave a file of another project",
            msg.FILE_OTHER_PROJECT,
            repacked(name, project=OTHER_PROJECT),
        ),
        "pprl-other-set": (
            "gave a file made with an earlier set of bundles",
            msg.FILE_OTHER_SET,
            repacked(name, set=OTHER_SET),
        ),
        "pprl-other-site": (
            "gave another site's file under its name",
            msg.FILE_OF_OTHER_SITE,
            repacked(name, site="site_c"),
        ),
        "pprl-other-release": (
            "gave a file written by another release",
            (msg.FILE_OTHER_RELEASE, "pprl 0.0.1"),
            repacked(name, release="0.0.1"),
        ),
    }
    return [
        Case(command, key, did, expect.get(key, says), files={name: data})
        for key, (did, says, data) in usual.items()
        if key not in skip
    ]


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------

SELECTED = "Wrote 1 table to site_a.selected.pprl, for 10 patients."


def exported(text: str) -> dict:
    """Make case files: tables/scores.csv, the table that select reads."""
    return {"files": {"tables/scores.csv": text}}


def _scores_selection(**selection: object) -> dict:
    """Make case arguments: the bundle's `tables`, with the scores ``selection``.

    ``selection`` gives the scores table's `keep` and `censor`.
    """
    return {"bundle": {"tables": {"scores": SITE_TABLES["scores"] | selection}}}


SELECT = [
    setting_case("select"),
    Case(
        "select",
        "input-missing",
        "has no scores table in tables/",
        (msg.SELECT_TABLE_MISSING, "tables/scores.csv"),
        files={"tables/scores.csv": DROP},
    ),
    Case(
        "select", "input-empty", "gave an empty table", msg.FILE_EMPTY, **exported("")
    ),
    Case(
        "select",
        "header-only",
        "gave a table with only a header (written as it is)",
        "Wrote 1 table to site_a.selected.pprl, for 0 patients.",
        **exported("person_id,score\n"),
        accepted=True,
    ),
    Case(
        "select",
        "output-exists",
        "already has the file it writes",
        (msg.OUTPUT_EXISTS, "site_a.selected.pprl"),
        files={"site_a.selected.pprl": "earlier output\n"},
    ),
    Case(
        "select",
        "missing-column",
        "gave a table without its score column",
        (msg.MISSING_COLUMNS, "missing the column score"),
        **exported("person_id\n1\n"),
    ),
    Case(
        "select",
        "extra-columns",
        "gave a table with a column the bundle does not name",
        (msg.UNEXPECTED_COLUMNS, "ward"),
        files={"tables/scores.csv": with_column("tables/scores.csv")},
    ),
    Case(
        "select",
        "duplicate-id",
        "gave two rows with the same primary key",
        (msg.SELECT_KEY_REPEATED, "rows 2, 3"),
        **exported("person_id,score\n1,5\n1,7\n2,3\n"),
    ),
    Case(
        "select",
        "duplicate-id",
        "gave two rows with the same primary key, one left out by a rule",
        "Wrote 1 table to site_a.selected.pprl, for 2 patients.",
        **_scores_selection(keep=[{"not_empty": "score"}]),
        **exported("person_id,score\n1,5\n1,\n2,3\n"),
        accepted=True,
    ),
    Case(
        "select",
        "selection",
        "has rows its rules leave out, said rule by rule",
        (
            "scores: kept 2 of 4 rows.",
            "Left out 1 row: score is empty.",
            "Left out 1 row: score is not one of 2 listed values.",
            "for 2 patients",
        ),
        **_scores_selection(
            keep=[{"not_empty": "score"}, {"one_of": {"score": ["5", "7"]}}]
        ),
        **exported("person_id,score\n1,5\n2,\n3,9\n4,7\n"),
        accepted=True,
    ),
    Case(
        "select",
        "selection",
        "has a value its one_of rule does not list, and none of one it does: "
        "a renamed value, warned of by count",
        (
            "Left out 2 rows: score is not one of 2 listed values.",
            msg.SELECT_VALUES_UNLISTED,
            "had 1 value",
            "no row of 7",
            _wording.ESCALATE,
            "for 2 patients",
        ),
        **_scores_selection(keep=[{"one_of": {"score": ["5", "7"]}}]),
        **exported("person_id,score\n1,5\n2,8\n3,8\n4,5\n"),
        accepted=True,
    ),
    Case(
        "select",
        "selection",
        "has a censored column, emptied",
        ("Emptied score in every row kept.", SELECTED),
        **_scores_selection(censor=["score"]),
        accepted=True,
    ),
    Case(
        "select",
        "selection",
        "has a bundle whose rule names a column the table does not have",
        (msg.RULE_INVALID, "names ward", _wording.ESCALATE),
        **_scores_selection(keep=[{"not_empty": "ward"}]),
    ),
    Case(
        "select",
        "valid",
        "selected the rows of the site's tables",
        (SELECTED, "scores: kept 10 of 10 rows."),
        accepted=True,
        creates="site_a.selected.pprl",
    ),
]

# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------

ALL_CLEAR = "Wrote 10 rows to site_a.clean.pprl"


def raw_file(text: str) -> dict:
    """Make case files: patients.csv, the raw export clean reads, as ``text``."""
    return {"files": {"patients.csv": text}}


def raw(*rows: str) -> dict:
    """Make case files: patients.csv, the raw export that clean reads."""
    return {"files": {"patients.csv": patients(*rows)}}


SELECTED_FILE = "site_a.selected.pprl"


def _only_patients(count: int) -> Callable[[Path], bytes]:
    """Make file bytes for site_a's selected tables, with only some patients.

    Only the first ``count`` patients are the study's patients.
    """
    return repacked(SELECTED_FILE, lambda df: df.iloc[:count], member=Member.COHORT)


CLEAN = [
    setting_case("clean"),
    Case(
        "clean",
        "input-missing",
        "has no selected tables file (pprl select was not run)",
        (msg.FILE_NOT_FOUND, SELECTED_FILE),
        files={SELECTED_FILE: DROP},
    ),
    Case(
        "clean",
        "selection",
        "gave patients that pprl select did not keep (left out)",
        (msg.CLEAN_NOT_SELECTED, "Left out 6 rows of patients.csv", "Wrote 4 rows"),
        files={SELECTED_FILE: _only_patients(4)},
        accepted=True,
    ),
    Case(
        "clean",
        "selection",
        "has no row for some of the study's patients",
        (msg.CLEAN_NO_PATIENT, "for 8 patients"),
        **raw(JOHN, JANE),
        accepted=True,
    ),
    Case(
        "clean",
        "input-missing",
        "has no patients.csv",
        (msg.FILE_NOT_FOUND, "patients.csv"),
        files={"patients.csv": DROP},
    ),
    Case(
        "clean",
        "input-empty",
        "gave an empty patient file",
        (msg.FILE_EMPTY, "patients.csv"),
        files={"patients.csv": ""},
    ),
    Case(
        "clean",
        "header-only",
        "gave a patient file with only a header",
        msg.FILE_NO_ROWS,
        files={"patients.csv": PATIENTS_HEADER},
    ),
    Case(
        "clean",
        "input-malformed",
        "gave a malformed CSV",
        (msg.FILE_MALFORMED, "patients.csv"),
        files={"patients.csv": MALFORMED_CSV},
    ),
    Case(
        "clean",
        "output-exists",
        "already has the cleaned file",
        (msg.OUTPUT_EXISTS, "site_a.clean.pprl"),
        files={"site_a.clean.pprl": "earlier output\n"},
    ),
    Case(
        "clean",
        "output-exists",
        "already has the set-aside file",
        (msg.OUTPUT_EXISTS, "site_a.set_aside.csv"),
        files={"site_a.set_aside.csv": "earlier output\n"},
    ),
    Case(
        "clean",
        "missing-column",
        "gave a file without the fields' columns",
        (msg.MISSING_COLUMNS, "missing the columns name, city, state, zip, dob"),
        files={"patients.csv": "person_id,nickname\n1,Jo\n"},
    ),
    Case(
        "clean",
        "missing-column",
        "gave a file without its id column",
        (msg.MISSING_COLUMNS, "missing the column person_id"),
        files={"patients.csv": "patient_id,name,city,state,zip,dob\n1,Jo,,,,\n"},
    ),
    Case(
        "clean",
        "extra-columns",
        "gave a patient file with a column that is not a field",
        (msg.UNEXPECTED_COLUMNS, "ward"),
        files={"patients.csv": with_column("patients_a.csv")},
    ),
    Case(
        "clean",
        "duplicate-id",
        "gave two rows with the same person_id (both set aside)",
        (msg.CLEAN_SET_ASIDE, "its person_id is also used on another row: rows 2, 3"),
        **raw(JOHN, JANE_AS_1),
        accepted=True,
    ),
    Case(
        "clean",
        "bad-format",
        "wrote a date of birth as mm/dd/yyyy (set aside, not guessed)",
        (msg.CLEAN_SET_ASIDE, "its dob is not a date written yyyy-mm-dd: row 2"),
        **raw(JOHN.replace("1990-01-01", "03/15/1990"), JANE),
        accepted=True,
        creates="site_a.set_aside.csv",
    ),
    *(
        Case("clean", "repairs", did, says, **raw(row, JANE), accepted=True)
        for did, says, row in (
            (
                "gave a ZIP+4 code (shortened)",
                (msg.CLEAN_REPAIRED, "shortened a ZIP+4 code to its first 5 digits"),
                JOHN.replace("02101", "02101-1234"),
            ),
            (
                "gave a state in lowercase (capitalized)",
                (msg.CLEAN_REPAIRED, "wrote the state code in capitals"),
                JOHN.replace(",MA,", ",ma,"),
            ),
            (
                "put spaces around a state and a person_id (removed)",
                (
                    msg.CLEAN_REPAIRED,
                    "In the person_id column, removed spaces",
                    "In the state column, removed spaces",
                ),
                " " + JOHN.replace(",MA,", ", MA ,"),
            ),
            (
                "gave a date of birth with a time, as Excel writes it (removed)",
                (msg.CLEAN_REPAIRED, "removed a time after the date"),
                JOHN.replace("1990-01-01", "1990-01-01 00:00:00"),
            ),
            (
                "gave a ZIP code of 4 digits (set aside, not guessed)",
                (msg.CLEAN_SET_ASIDE, "so it may have lost a leading zero"),
                JOHN.replace("02101", "2101"),
            ),
            (
                "wrote a state in full (set aside, not guessed)",
                (msg.CLEAN_SET_ASIDE, "its state is not a 2-letter state code"),
                JOHN.replace(",MA,", ",Massachusetts,"),
            ),
            (
                "gave a row without a person_id (left out: not in the study)",
                (msg.CLEAN_NOT_SELECTED, "Left out 1 row of patients.csv"),
                JOHN.replace("1,", ",", 1),
            ),
            (
                "gave a row with no identifying value (set aside)",
                (msg.CLEAN_SET_ASIDE, "every identifying value is empty"),
                "1,,,,,",
            ),
            (
                "gave names with spaces, accents and odd characters (kept as they are)",
                "Wrote 2 rows to site_a.clean.pprl",
                JOHN.replace("John Example", " José ??? O'Example "),
            ),
            (
                "gave a placeholder name, NULL (kept: names are never judged)",
                "Wrote 2 rows to site_a.clean.pprl",
                JOHN.replace("John Example", "NULL"),
            ),
        )
    ),
    Case(
        "clean",
        "valid",
        "cleaned a patient file that needed no changes",
        ALL_CLEAR,
        accepted=True,
        creates="site_a.clean.pprl",
    ),
]

# ---------------------------------------------------------------------------
# hash
# ---------------------------------------------------------------------------

# The message says "Wrote 1 hash" for one patient and "Wrote 2 hashes" for two.
HASHED = "from site_a to outbox/linkage/site_a.hashes.pprl."
CLEANED_FILE = "site_a.clean.pprl"


def _clean_file(text: str) -> Callable[[Path], bytes]:
    """Make file bytes for a cleaned patient file of site_a, as pprl clean writes it.

    The file's rows are the CSV ``text``.
    """

    def data(folder: Path) -> bytes:
        rows = read_strings_csv(io.StringIO(text))
        staged = Path(folder) / ".cleaned.pprl"
        _pprl_file.write(
            staged,
            "clean",
            {"patients": rows},
            provenance=provenance("site_a"),
        )
        result = staged.read_bytes()
        staged.unlink()
        return result

    return data


def cleaned(text: str) -> dict:
    """Make case files: site_a.clean.pprl, the cleaned file that hash reads.

    The file holds the rows of the CSV ``text``.
    """
    return {"files": {CLEANED_FILE: _clean_file(text)}}


HASH = [
    *bundle_cases(),
    setting_case("hash"),
    *pprl_cases("hash", CLEANED_FILE, skip=("pprl-other-kind",)),
    Case(
        "hash",
        "pprl-other-kind",
        "gave its packed tables under the cleaned file's name",
        msg.FILE_OTHER_KIND,
        files={CLEANED_FILE: repacked(CLEANED_FILE, kind="tables")},
    ),
    Case(
        "hash",
        "input-missing",
        "has no cleaned file (pprl clean was not run)",
        (msg.FILE_NOT_FOUND, CLEANED_FILE),
        files={CLEANED_FILE: DROP},
    ),
    Case(
        "hash",
        "input-missing",
        "has no secret file (the coordinator has not sent it)",
        (msg.FILE_NOT_FOUND, "site_a.secret.pprl"),
        files={"site_a.secret.pprl": DROP},
    ),
    Case(
        "hash",
        "input-missing",
        "has no private key (it was deleted)",
        (msg.FILE_NOT_FOUND, "site_a.private_key.pprl"),
        files={"site_a.private_key.pprl": DROP},
    ),
    Case(
        "hash",
        "input-empty",
        "gave an empty cleaned file",
        msg.NOT_A_PPRL_FILE,
        files={CLEANED_FILE: ""},
    ),
    Case(
        "hash",
        "header-only",
        "gave a cleaned file without rows",
        msg.FILE_NO_ROWS,
        **cleaned(CLEANED_HEADER),
    ),
    Case(
        "hash",
        "missing-column",
        "gave a cleaned file without the fields' columns",
        (msg.MISSING_COLUMNS, "missing the columns name, city, state, zip, dob"),
        **cleaned("study_id,nickname\n1,Jo\n"),
    ),
    Case(
        "hash",
        "missing-column",
        "gave a cleaned file without study_id",
        (msg.MISSING_COLUMNS, "missing the column study_id"),
        **cleaned(patients(JOHN)),
    ),
    Case(
        "hash",
        "duplicate-id",
        "gave two rows with the same study_id",
        (msg.DUPLICATE_IDS, "of study_id on more than one row"),
        **cleaned(cleaned_patients(JOHN, JANE_AS_1)),
    ),
    Case(
        "hash",
        "extra-columns",
        "gave a column that is not a field",
        (msg.UNEXPECTED_COLUMNS, "ward"),
        files={
            CLEANED_FILE: repacked(
                CLEANED_FILE, lambda df: df.assign(ward="7"), member="patients"
            )
        },
    ),
    *(
        Case("hash", "bad-format", did, says, **cleaned(text))
        for did, says, text in (
            (
                "wrote a date of birth as mm/dd/yyyy",
                (msg.BAD_FORMAT, "dob column", "row 2"),
                cleaned_patients(JOHN.replace("1990-01-01", "03/15/1990")),
            ),
            (
                "gave a date of birth that does not exist",
                (msg.BAD_FORMAT, "dob column", "row 2"),
                cleaned_patients(JOHN.replace("1990-01-01", "1990-02-30")),
            ),
            (
                "gave a ZIP code that lost its leading zero",
                (msg.BAD_FORMAT, "zip column", "row 2"),
                cleaned_patients(JOHN.replace("02101", "2101")),
            ),
            (
                "wrote a state in full",
                (msg.BAD_FORMAT, "state column", "row 2"),
                cleaned_patients(JOHN.replace(",MA,", ",Massachusetts,")),
            ),
            (
                "gave a ZIP+4 code (pprl clean shortens it)",
                (msg.BAD_FORMAT, "zip column", "row 2"),
                cleaned_patients(JOHN.replace("02101", "02101-1234")),
            ),
            (
                "wrote a state in lowercase (pprl clean capitalizes it)",
                (msg.BAD_FORMAT, "state column", "row 2"),
                cleaned_patients(JOHN.replace(",MA,", ",ma,")),
            ),
            (
                "put spaces around a state (pprl clean removes them)",
                (msg.BAD_FORMAT, "state column", "row 2"),
                cleaned_patients(JOHN.replace(",MA,", ", MA,")),
            ),
            (
                "put spaces around a study_id",
                (msg.IDS_INVALID, "row 2"),
                cleaned_patients(" " + JOHN, JANE),
            ),
            (
                "gave a row with no identifying value (pprl clean sets it aside)",
                (msg.NO_IDENTIFIERS, "row 3"),
                cleaned_patients(JOHN, "3,,,,,"),
            ),
            (
                "listed every row in the wrong format, not just the first",
                (msg.BAD_FORMAT, "rows 2, 3"),
                cleaned_patients(
                    JOHN.replace("02101", "2101"), JANE.replace("01970", "1970")
                ),
            ),
        )
    ),
    Case(
        "hash",
        "bad-format",
        "left a date of birth empty (a missing value)",
        HASHED,
        **cleaned(cleaned_patients(JOHN.replace("1990-01-01", ""))),
        accepted=True,
    ),
    Case(
        "hash",
        "output-exists",
        "already has the hash file",
        (msg.OUTPUT_EXISTS, "outbox/linkage/site_a.hashes.pprl"),
        files={"outbox/linkage/site_a.hashes.pprl": "earlier output\n"},
    ),
    Case(
        "hash",
        "valid",
        "gave names with spaces, accents and odd characters (kept as they are)",
        HASHED,
        **cleaned(
            cleaned_patients(JOHN.replace("John Example", " José ??? O'Example "))
        ),
        accepted=True,
    ),
    Case(
        "hash",
        "valid",
        "hashed a cleaned file",
        HASHED,
        accepted=True,
        creates="outbox/linkage/site_a.hashes.pprl",
    ),
]

# ---------------------------------------------------------------------------
# pack
# ---------------------------------------------------------------------------

PACKED = (
    "Wrote 1 table to outbox/merge/site_a.tables.pprl, with study_ids in place of "
    "person_id."
)


def _selected_file(text: str) -> Callable[[Path], bytes]:
    """Make file bytes for site_a's selected tables file, as pprl select writes it.

    Its scores table holds the rows of the CSV ``text``.
    """

    def data(folder: Path) -> bytes:
        rows = read_strings_csv(io.StringIO(text))
        staged = Path(folder) / ".selected.pprl"
        write_selected(staged, "site_a", {"scores": rows})
        result = staged.read_bytes()
        staged.unlink()
        return result

    return data


def _recleaned(folder: Path) -> bytes:
    """Make file bytes for site_a's cleaned file, from the workspace's selected file.

    The file is written as clean writes it.
    """
    original = _pprl_file.read(Path(folder) / CLEANED_FILE)
    selected_file = _pprl_file.read(Path(folder) / SELECTED_FILE, lazy=True)
    staged = Path(folder) / ".cleaned.pprl"
    _pprl_file.write(
        staged,
        "clean",
        original.members,
        provenance=provenance("site_a"),
        inputs=[selected_file],
    )
    result = staged.read_bytes()
    staged.unlink()
    return result


def selected(text: str) -> dict:
    """Make case files: site_a.selected.pprl, the selected tables that pack reads.

    It holds the rows of the CSV ``text``, and comes with the cleaned file
    made from it.
    """
    return {"files": {SELECTED_FILE: _selected_file(text), CLEANED_FILE: _recleaned}}


PACK = [
    setting_case("pack"),
    Case(
        "pack",
        "input-missing",
        "has no selected tables file (pprl select was not run)",
        (msg.FILE_NOT_FOUND, SELECTED_FILE),
        files={SELECTED_FILE: DROP},
    ),
    Case(
        "pack",
        "input-missing",
        "has no cleaned file (pprl clean was not run)",
        (msg.FILE_NOT_FOUND, CLEANED_FILE),
        files={CLEANED_FILE: DROP},
    ),
    Case(
        "pack",
        "input-empty",
        "gave an empty selected tables file",
        msg.NOT_A_PPRL_FILE,
        files={SELECTED_FILE: ""},
    ),
    Case(
        "pack",
        "header-only",
        "gave a selected table without rows (written as it is)",
        PACKED,
        **selected("person_id,score\n"),
        accepted=True,
    ),
    Case(
        "pack",
        "output-exists",
        "already has the file it writes",
        (msg.OUTPUT_EXISTS, "outbox/merge/site_a.tables.pprl"),
        files={"outbox/merge/site_a.tables.pprl": "earlier output\n"},
    ),
    Case(
        "pack",
        "missing-column",
        "gave a selected table without its score column",
        (msg.FILE_DAMAGED, SELECTED_FILE),
        **selected("person_id\n1\n"),
    ),
    Case(
        "pack",
        "extra-columns",
        "gave a selected table with a column the bundle does not name",
        (msg.FILE_DAMAGED, SELECTED_FILE),
        **selected("person_id,score,ward\n1,5,7\n"),
    ),
    Case(
        "pack",
        "duplicate-id",
        "gave a selected table whose primary key repeats",
        (msg.PACK_NOT_SELECTED, "2 rows of the table scores"),
        **selected("person_id,score\n1,5\n1,7\n2,3\n"),
    ),
    Case(
        "pack",
        "selection",
        "gave a selected row that the project's rules leave out",
        (msg.PACK_NOT_SELECTED, "1 row of the table scores"),
        bundle={
            "tables": {
                "scores": SITE_TABLES["scores"] | {"keep": [{"not_empty": "score"}]}
            }
        },
        **selected("person_id,score\n1,5\n2,\n"),
    ),
    Case(
        "pack",
        "selection",
        "gave a selected row with a value in a censored column",
        (msg.PACK_NOT_SELECTED, "1 row of the table scores"),
        bundle={"tables": {"scores": SITE_TABLES["scores"] | {"censor": ["score"]}}},
        **selected("person_id,score\n1,\n2,3\n"),
    ),
    Case(
        "pack",
        "unknown-ids",
        "gave rows whose person_id has no study_id (left out)",
        (msg.PACK_LEFT_OUT, "Left out 2 rows of the table scores"),
        **selected("person_id,score\n1,5\n999,7\n,3\n"),
        accepted=True,
    ),
    Case(
        "pack",
        "other-selection",
        "ran select again after clean",
        (msg.PACK_OTHER_SELECTION, "site_a.clean.pprl was not made from"),
        files={CLEANED_FILE: repacked(CLEANED_FILE, inputs=[])},
    ),
    Case(
        "pack",
        "valid",
        "packed the site's selected tables",
        PACKED,
        accepted=True,
        creates="outbox/merge/site_a.tables.pprl",
    ),
]

# ---------------------------------------------------------------------------
# link and sweep, which both read hash files
# ---------------------------------------------------------------------------


def hash_file(name: str, data: object) -> dict:
    """Make case arguments: a hash file ``name`` given to sweep along with site_b's."""
    return {"files": {name: data}, "config": {"hashes": [name, "site_b.hashes.pprl"]}}


LINKED = "Wrote outbox/merge/linkages.pprl."
SITE_B_HASHES = "site_b.hashes.pprl"


def copied(name: str) -> Callable[[Path], bytes]:
    """Make file bytes: the workspace's file ``name``, unchanged."""

    def data(folder: Path) -> bytes:
        return (Path(folder) / name).read_bytes()

    return data


def site_b_hashes(data: object) -> dict:
    """Make case files: site_b.hashes.pprl, one of the two files that link reads."""
    return {"files": {SITE_B_HASHES: data}}


LINK = [
    setting_case("link"),
    Case(
        "link",
        "different-secrets",
        "linked a site's hashes made with another secret (nothing links)",
        (msg.NOTHING_LINKED_PAIR, "site_a and site_b: no records linked"),
        **site_b_hashes(copied("other_secret.hashes.pprl")),
        accepted=True,
    ),
    Case(
        "link",
        "different-secrets",
        "linked hashes made with one secret (each pair's count shown)",
        (msg.LINKED_PAIR, "site_a and site_b: 8 records linked"),
        accepted=True,
    ),
    Case(
        "link",
        "input-missing",
        "has no hash file from site_b",
        (msg.FILE_NOT_FOUND, SITE_B_HASHES),
        files={SITE_B_HASHES: DROP},
    ),
    Case(
        "link",
        "input-empty",
        "gave an empty hash file",
        msg.NOT_A_PPRL_FILE,
        **site_b_hashes(""),
    ),
    Case(
        "link",
        "output-exists",
        "already has the linkage file",
        (msg.OUTPUT_EXISTS, "outbox/merge/linkages.pprl"),
        files={"outbox/merge/linkages.pprl": "earlier output\n"},
    ),
    Case(
        "link",
        "hash-length",
        "linked 1024-bit hashes with 512-bit ones",
        (
            msg.HASH_LENGTH_MISMATCH,
            "site_a.hashes.pprl has 1024 bits, site_b.hashes.pprl has 512 bits",
        ),
        **site_b_hashes(copied("512.hashes.pprl")),
    ),
    Case(
        "link",
        "valid",
        "linked two sites (one file, for the merge agent)",
        LINKED,
        accepted=True,
        creates="outbox/merge/linkages.pprl",
    ),
]

SWEPT = "0.950        8"  # The results table's row at threshold 0.95

# A schema sweep over the workspace's patient files.
GRID = {
    "hashes": DROP,
    "patients": ["patients_a.csv", "patients_b.csv"],
    "id": "person_id",
    "assessment": False,
    "fields": {"name": 0.5, "dob": {"weight": 0.25, "type": "date"}, "city": 0.25},
    "hash_length": 1024,
    "fill_ratio": 1.0,
}

ALSO_WEIGHTS = {"name": 0.45, "dob": {"weight": 0.2, "type": "date"}, "city": 0.35}

SWEEP = [
    *config_cases("sweep", missing=None, outside=None, absolute=None),
    Case(
        "sweep",
        "config-missing",
        "has no sweep.yml",
        (msg.CONFIG_NOT_FOUND, "sweep.yml"),
        config_text=DROP,
    ),
    Case(
        "sweep",
        "missing-key",
        "left out thresholds",
        (msg.THRESHOLDS_INVALID, "is missing"),
        config={"thresholds": DROP},
    ),
    Case(
        "sweep",
        "missing-key",
        "left out hashes (so it expects a schema sweep over patient files)",
        msg.SWEEP_PATIENTS_NOT_LIST,
        config={"hashes": DROP},
    ),
    Case(
        "sweep",
        "wrong-type",
        "gave thresholds as text",
        (msg.THRESHOLDS_INVALID, "high"),
        config={"thresholds": "high"},
    ),
    Case(
        "sweep",
        "out-of-range",
        "gave a threshold above 1",
        (msg.THRESHOLDS_INVALID, "[0.9, 1.5]"),
        config={"thresholds": [0.9, 1.5]},
    ),
    Case(
        "sweep",
        "too-few",
        "gave one hash file",
        msg.HASHES_NOT_LIST,
        config={"hashes": ["site_a.hashes.pprl"]},
    ),
    Case(
        "sweep",
        "outside-path",
        "gave a hash file as an absolute path",
        msg.NAME_OUTSIDE,
        config={"hashes": ["{workspace}/site_a.hashes.pprl", "site_b.hashes.pprl"]},
    ),
    Case(
        "sweep",
        "outside-path",
        "gave output as an absolute path",
        msg.NAME_OUTSIDE,
        config={"output": OUTSIDE + ".csv"},
    ),
    Case(
        "sweep",
        "outside-path",
        "gave a hash file as ../outside.pprl",
        msg.NAME_OUTSIDE,
        **hash_file("../outside.pprl", copied("site_a.hashes.pprl")),
    ),
    Case(
        "sweep",
        "input-missing",
        "named a hash file that is not there",
        (msg.FILE_NOT_FOUND, "missing.pprl"),
        config={"hashes": ["site_a.hashes.pprl", "missing.pprl"]},
    ),
    Case(
        "sweep",
        "input-empty",
        "gave an empty hash file",
        msg.NOT_A_PPRL_FILE,
        **hash_file("empty.pprl", ""),
    ),
    Case(
        "sweep",
        "header-only",
        "gave a hash file without hashes",
        msg.FILE_NO_ROWS,
        **hash_file(
            "none.hashes.pprl", repacked("site_a.hashes.pprl", lambda df: df.iloc[0:0])
        ),
    ),
    Case(
        "sweep",
        "output-exists",
        "already has the output file",
        (msg.OUTPUT_EXISTS, "sweep.csv"),
        files={"sweep.csv": "earlier output\n"},
    ),
    *(
        Case(
            "sweep",
            input_key,
            f"swept schema settings over a patient file that {did}",
            says,
            files={"odd.csv": text},
            config=GRID | {"patients": ["odd.csv", "patients_b.csv"]},
        )
        for input_key, did, says, text in (
            (
                "missing-column",
                "has no person_id column",
                (msg.MISSING_COLUMNS, "missing the column person_id"),
                "name,city,dob\nJo,Boston,1990-01-01\n",
            ),
            (
                "duplicate-id",
                "gives two rows one person_id",
                (msg.DUPLICATE_IDS, "of person_id on more than one row"),
                patients(JOHN, JANE_AS_1),
            ),
        )
    ),
    Case(
        "sweep",
        "extra-columns",
        "swept schema settings over patient files with columns it does not hash",
        "Suggested: threshold=0.9, name=0.5, dob=0.25, city=0.25",
        files={"odd.csv": with_column("patients_a.csv")},
        config=GRID | {"patients": ["odd.csv", "patients_b.csv"]},
        accepted=True,
        creates="sweep.csv",
    ),
    Case(
        "sweep",
        "assessment",
        "asked for precision and recall of hash files",
        msg.SWEEP_NO_ASSESSMENT,
        config={"assessment": True},
    ),
    Case(
        "sweep",
        "assessment",
        "asked for precision and recall in a schema sweep",
        "100.00%",
        config=GRID | {"assessment": True},
        accepted=True,
        creates="sweep.csv",
    ),
    Case(
        "sweep",
        "missing-key",
        "left out the id column in a schema sweep",
        msg.ID_COLUMN_MISSING,
        config=GRID | {"id": DROP},
    ),
    Case(
        "sweep",
        "valid",
        "swept two thresholds",
        SWEPT,
        accepted=True,
        creates="sweep.csv",
    ),
    Case(
        "sweep",
        "valid",
        "swept schema settings over patient files",
        "Suggested: threshold=0.9, name=0.5, dob=0.25, city=0.25",
        config=GRID,
        accepted=True,
        creates="sweep.csv",
    ),
    Case(
        "sweep",
        "valid",
        "swept two sets of weights over patient files",
        "Configuration 2/2 (name=0.45, dob=0.2, city=0.35): done",
        config=GRID | {"fields": [GRID["fields"], ALSO_WEIGHTS]},
        accepted=True,
        creates="sweep.csv",
    ),
    Case(
        "sweep",
        "wrong-type",
        "gave a set of weights that is not one in a schema sweep",
        msg.FIELDS_NOT_SETTINGS,
        config=GRID | {"fields": [GRID["fields"], "name"]},
    ),
    Case(
        "sweep",
        "missing-key",
        "left out the hash length in a schema sweep",
        (msg.HASH_LENGTH_INVALID, "is missing"),
        config=GRID | {"hash_length": DROP},
    ),
    Case(
        "sweep",
        "out-of-range",
        "gave a fill ratio above the limit in a schema sweep",
        (msg.FILL_RATIO_INVALID, "is 5"),
        config=GRID | {"fill_ratio": 5},
    ),
    Case(
        "sweep",
        "wrong-type",
        "gave assessment as text in a schema sweep",
        (msg.CONFIG_NOT_TRUE_FALSE, "`assessment`"),
        config=GRID | {"assessment": "yes"},
    ),
    Case(
        "sweep",
        "bad-format",
        "swept schema settings over a patient file whose ZIP code lost its "
        "leading zero",
        (msg.BAD_FORMAT, "zip column", "zips.csv"),
        files={"zips.csv": patients(JOHN.replace("02101", "2101"), JANE)},
        config=GRID
        | {
            "patients": ["zips.csv", "patients_b.csv"],
            "fields": {"name": 0.5, "zip": {"weight": 0.5, "type": "zip"}},
        },
    ),
]

# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

MERGED = "merged into 12 rows"


def scores_config(score: dict) -> dict:
    """Make merge rules for the scores table, with ``score`` as its score rule."""
    return {
        "tables": {
            "scores": {
                "primary_key": ["person_id"],
                "column": {"score": score},
            }
        }
    }


def site_table(site: str, change: Callable[[pd.DataFrame], pd.DataFrame]) -> dict:
    """Make case files: a site's packed tables, with its scores table changed.

    The scores table is passed through ``change``.
    """
    name = f"{site}.tables.pprl"
    return {name: repacked(name, change, member="scores")}


def _tables_without_scores(folder: Path) -> bytes:
    """Make file bytes for site_b's packed tables, without its scores table."""
    original = _pprl_file.read(Path(folder) / "site_b.tables.pprl")
    staged = Path(folder) / ".tables.pprl"
    _pprl_file.write(
        staged,
        "tables",
        {"visits": original.members["scores"].rename(columns={"score": "visit"})},
        provenance=provenance("site_b"),
        inputs=original.header["inputs"],
    )
    data = staged.read_bytes()
    staged.unlink()
    return data


def _tables_after_recleaning(folder: Path) -> bytes:
    """Make file bytes for site_a's packed tables, after site_a ran clean again.

    They are written as pack writes them, but from a different cleaned file
    than the one the linked hashes came from.
    """
    original = _pprl_file.read(Path(folder) / "site_a.tables.pprl")
    staged = Path(folder) / ".recleaned.pprl"
    written = {"provenance": provenance("site_a")}
    rows = pd.DataFrame({"study_id": ["1"], "name": ["x"]})
    _pprl_file.write(staged, "clean", {"patients": rows}, **written)
    again = _pprl_file.read(staged)
    staged.unlink()
    _pprl_file.write(staged, "tables", original.members, inputs=[again], **written)
    data = staged.read_bytes()
    staged.unlink()
    return data


MERGE = [
    setting_case("merge"),
    Case(
        "merge",
        "other-run",
        "gave site_a's tables, packed after site_a ran clean again",
        (msg.FILE_OTHER_RUN, "site_a.tables.pprl and the linkage file"),
        files={"site_a.tables.pprl": _tables_after_recleaning},
    ),
    Case(
        "merge",
        "merge-config",
        "asked for a strategy that does not exist",
        (msg.STRATEGY_UNKNOWN, "take_lowest"),
        bundle=scores_config({"rank_by": [{"take_lowest": {"column": "score"}}]}),
    ),
    Case(
        "merge",
        "merge-config",
        "used take_highest",
        MERGED,
        bundle=scores_config({"rank_by": [{"take_highest": {"column": "score"}}]}),
        accepted=True,
    ),
    Case(
        "merge",
        "merge-config",
        "used take_first_site",
        MERGED,
        bundle=scores_config({"rank_by": [{"take_first_site": {}}]}),
        accepted=True,
    ),
    Case(
        "merge",
        "merge-config",
        "used take_latest",
        MERGED,
        files={
            **site_table("site_a", lambda df: df.assign(date="2024-01-01")),
            **site_table("site_b", lambda df: df.assign(date="2024-02-01")),
        },
        bundle={
            "tables": {
                "scores": {
                    "primary_key": ["person_id"],
                    "column": {
                        "score": {"rank_by": [{"take_latest": {"column": "date"}}]},
                        "date": {"tether": "score"},
                    },
                }
            }
        },
        accepted=True,
    ),
    Case(
        "merge",
        "merge-config",
        "used take_top_ordinal",
        MERGED,
        bundle=scores_config(
            {
                "rank_by": [
                    {
                        "take_top_ordinal": {
                            "column": "score",
                            "scale": [str(n) for n in range(40)],
                        }
                    }
                ]
            }
        ),
        accepted=True,
    ),
    Case(
        "merge",
        "input-missing",
        "has not received the linkage file",
        (msg.FILE_NOT_FOUND, "linkages.pprl"),
        files={"linkages.pprl": DROP},
    ),
    Case(
        "merge",
        "input-missing",
        "has not received site_b's tables",
        (msg.FILE_NOT_FOUND, "site_b.tables.pprl"),
        files={"site_b.tables.pprl": DROP},
    ),
    Case(
        "merge",
        "input-empty",
        "gave an empty linkage file",
        msg.NOT_A_PPRL_FILE,
        files={"linkages.pprl": ""},
    ),
    Case(
        "merge",
        "header-only",
        "gave a linkage file that links nobody",
        "Kept 20 records not in the linkage file",
        files={"linkages.pprl": repacked("linkages.pprl", lambda df: df.iloc[0:0])},
        accepted=True,
    ),
    Case(
        "merge",
        "output-exists",
        "already has an outbox/host/merged/ folder",
        (msg.OUTPUT_EXISTS, "outbox/host/merged"),
        files={"outbox/host/merged/scores.csv": "earlier output\n"},
    ),
    Case(
        "merge",
        "missing-column",
        "gave one site's table without study_id",
        (msg.MISSING_COLUMNS, "scores is missing the column study_id"),
        files=site_table("site_a", lambda df: df.drop(columns="study_id")),
    ),
    Case(
        "merge",
        "extra-columns",
        "gave a site table with a column the merge rules do not declare",
        (msg.COLUMNS_UNDECLARED, "ward"),
        files=site_table("site_a", lambda df: df.assign(ward="7")),
    ),
    Case(
        "merge",
        "merge-config",
        "gave take_top_ordinal a scale that misses some values",
        (msg.VALUES_NOT_IN_SCALE, "site_a.tables.pprl, table scores"),
        bundle=scores_config(
            {
                "rank_by": [
                    {"take_top_ordinal": {"column": "score", "scale": ["Low", "High"]}}
                ]
            }
        ),
    ),
    Case(
        "merge",
        "merge-config",
        "gave take_latest no column to compare",
        msg.STRATEGY_SETTINGS,
        bundle=scores_config({"rank_by": [{"take_latest": {}}]}),
    ),
    Case(
        "merge",
        "merge-config",
        "wrote rank_by as one strategy, not a list",
        msg.RANK_BY_NOT_LIST,
        bundle=scores_config({"rank_by": {"take_highest": {"column": "score"}}}),
    ),
    Case(
        "merge",
        "merge-config",
        "named two strategies in one item of rank_by",
        (msg.RANK_BY_ITEM, "Item 1"),
        bundle=scores_config(
            {
                "rank_by": [
                    {
                        "take_highest": {"column": "score"},
                        "take_latest": {"column": "score"},
                    }
                ]
            }
        ),
    ),
    Case(
        "merge",
        "merge-config",
        "tethered score to a column that does not exist",
        (msg.TETHER_UNKNOWN, "nonexistent"),
        bundle=scores_config({"tether": "nonexistent"}),
    ),
    Case(
        "merge",
        "merge-config",
        "used take_highest on text",
        (msg.VALUES_NOT_NUMBERS, "in site_a.tables.pprl, table scores."),
        files=site_table("site_a", lambda df: df.assign(score="text")),
    ),
    Case(
        "merge",
        "merge-config",
        "declared a column with no rule",
        msg.COLUMN_NO_RULE,
        bundle=scores_config({}),
    ),
    Case(
        "merge",
        "merge-config",
        "gave a column rule an unknown key",
        (msg.COLUMN_RULE_UNKNOWN_KEYS, "strategy"),
        bundle=scores_config({"strategy": "take_highest"}),
    ),
    Case(
        "merge",
        "merge-config",
        "declared a column the data does not have",
        (msg.COLUMNS_NOT_IN_DATA, "ward"),
        bundle={
            "tables": {
                "scores": {
                    "primary_key": ["person_id"],
                    "column": {
                        "score": {"rank_by": [{"take_highest": {"column": "score"}}]},
                        "ward": {"drop": True},
                    },
                }
            }
        },
    ),
    Case(
        "merge",
        "merge-config",
        "gave a table an unknown key",
        (msg.TABLE_UNKNOWN_KEYS, "columns"),
        bundle={"tables": {"scores": {"primary_key": ["person_id"], "columns": {}}}},
    ),
    Case(
        "merge",
        "bad-format",
        "used take_latest on dates written mm/dd/yyyy",
        (msg.VALUES_NOT_DATES, "in site_a.tables.pprl, table scores."),
        files={
            **site_table("site_a", lambda df: df.assign(date="01/02/2024")),
            **site_table("site_b", lambda df: df.assign(date="2024-02-01")),
        },
        bundle={
            "tables": {
                "scores": {
                    "primary_key": ["person_id"],
                    "column": {
                        "score": {"rank_by": [{"take_latest": {"column": "date"}}]},
                        "date": {"tether": "score"},
                    },
                }
            }
        },
    ),
    Case(
        "merge",
        "merge-config",
        "left out primary_key",
        msg.PRIMARY_KEY_INVALID,
        bundle={"tables": {"scores": {"column": {"score": {"drop": True}}}}},
    ),
    Case(
        "merge",
        "merge-data",
        "has no scores table from site_b",
        (msg.SITE_TABLE_MISSING, "site_b.tables.pprl has no table scores"),
        files={"site_b.tables.pprl": _tables_without_scores},
    ),
    Case(
        "merge",
        "valid",
        "merged the scores table",
        MERGED,
        accepted=True,
        creates="outbox/host/merged/scores.csv",
    ),
]

# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------

# Lines that inspect shows. A pprl file's labels line up after "Bundle set:",
# and a CSV file's line up after "Columns:".
DESCRIBED = "Bits:        1024"


def described(label: str, value: object) -> str:
    """Write a line that inspect shows for a pprl file."""
    return f"{label:<11}  {value}"


INSPECT = [
    Case(
        "inspect",
        "no-arguments",
        "gave no file name",
        (msg.MISSING_ARGUMENTS, "the file to describe"),
        argv=("inspect",),
    ),
    Case(
        "inspect",
        "outside-path",
        "gave file as an absolute path",
        msg.NAME_OUTSIDE,
        argv=("inspect", "{workspace}/site_a.hashes.pprl"),
    ),
    Case(
        "inspect",
        "outside-path",
        "gave file as ../outside.csv",
        msg.NAME_OUTSIDE,
        files={"../outside.csv": patients(JOHN)},
        argv=("inspect", "../outside.csv"),
    ),
    Case(
        "inspect",
        "input-missing",
        "named a file that is not there",
        (msg.FILE_NOT_FOUND, "missing.pprl"),
        argv=("inspect", "missing.pprl"),
    ),
    Case(
        "inspect",
        "input-empty",
        "gave an empty file",
        msg.FILE_EMPTY,
        files={"empty.csv": ""},
        argv=("inspect", "empty.csv"),
    ),
    Case(
        "inspect",
        "input-malformed",
        "gave a malformed CSV",
        msg.FILE_MALFORMED,
        files={"bad.csv": MALFORMED_CSV},
        argv=("inspect", "bad.csv"),
    ),
    Case(
        "inspect",
        "pprl-damaged",
        "gave a hash file opened and saved in a spreadsheet",
        msg.FILE_DAMAGED,
        files={"site_a.hashes.pprl": _resaved_file("site_a.hashes.pprl")},
        argv=("inspect", "site_a.hashes.pprl"),
    ),
    Case(
        "inspect",
        "pprl-other-release",
        "described a file written by another release",
        described("Release:", "0.0.1"),
        files={"old.pprl": repacked("site_a.hashes.pprl", release="0.0.1")},
        argv=("inspect", "old.pprl"),
        accepted=True,
    ),
    Case(
        "inspect",
        "valid",
        "described a hash file",
        (
            DESCRIBED,
            described("Type:", "hash file"),
            described("Site:", "site_a"),
            described("Made from:", "cleaned patient file (site_a)"),
        ),
        argv=("inspect", "site_a.hashes.pprl"),
        accepted=True,
    ),
    Case(
        "inspect",
        "valid",
        "described a cleaned patient file",
        described("Holds:", "patients: 10 rows"),
        argv=("inspect", "site_a.clean.pprl"),
        accepted=True,
    ),
    Case(
        "inspect",
        "valid",
        "described a linkage file",
        described("Type:", "linkage file"),
        argv=("inspect", "linkages.pprl"),
        accepted=True,
    ),
    Case(
        "inspect",
        "valid",
        "described a CSV file",
        ("Type:     a CSV file", "Rows:     10"),
        argv=("inspect", "patients_a.csv"),
        accepted=True,
    ),
]

NOT_UTF8 = (msg.FILE_NOT_UTF8, "latin.csv")
LATIN = {
    "latin.csv": patients(JOHN.replace("John Example", "José Last")).encode("cp1252")
}

NOT_UTF8_CASES = [
    Case(
        "inspect",
        "not-utf8",
        "gave a file saved in a Windows encoding",
        NOT_UTF8,
        files=LATIN,
        argv=("inspect", "latin.csv"),
    ),
    Case(
        "clean",
        "not-utf8",
        "gave a patient file saved in a Windows encoding (converted to UTF-8)",
        (msg.CLEAN_CONVERTED, "patients.csv"),
        files={"patients.csv": LATIN["latin.csv"]},
        accepted=True,
    ),
]


# ---------------------------------------------------------------------------
# How a file was saved, for every command that reads one
# ---------------------------------------------------------------------------


def as_saved(name: str, change: Callable[[str], str]) -> Callable[[Path], str]:
    """Make file text: the workspace file ``name``, with ``change`` applied to it."""

    def text(folder: Path) -> str:
        return change(read_text(Path(folder) / name))

    return text


def _blank_row(text: str) -> str:
    header, rest = text.split("\n", 1)
    return f"{header}\n\n{rest}"


def _short_row(text: str) -> str:
    lines = text.split("\n")
    lines[1] = lines[1].rsplit(",", 1)[0]
    return "\n".join(lines)


# For each command: the file it reads (its fixed name, or for inspect, the name
# it is given), the input file it is a copy of, and what the command says when
# it reads that file without problems. select and sweep read CSV files with
# the same code, so they have no cases here.
SAVED_FILES = (
    ("clean", "patients.csv", "patients_a.csv", ALL_CLEAR),
    ("inspect", "saved.csv", "patients_a.csv", "Rows:     10"),
)


def _naming(command: str, name: str, text: str | Callable[[Path], str]) -> dict:
    """Make case arguments that give the file ``name``, with ``text``, to a command.

    inspect gets the name on the command line. clean reads it by its fixed
    name.
    """
    files = {name: text}
    if command == "inspect":
        return {"files": files, "argv": ("inspect", name)}
    return {"files": files}


SAVED_CASES = [
    Case(
        command,
        input_key,
        did,
        success if says is None else says,
        **_naming(command, name, as_saved(source, change)),
        accepted=says is None,
    )
    for command, name, source, success in SAVED_FILES
    for input_key, did, change, says in (
        (
            "saved-variants",
            (
                "gave a file with a byte-order mark and Windows line endings, as "
                "Excel's CSV UTF-8 saves it (read as usual)"
            ),
            lambda text: ("\ufeff" + text.replace("\n", "\r\n")).encode(),
            None,
        ),
        (
            "blank-rows",
            "gave a file with an empty row after the header",
            _blank_row,
            (msg.BLANK_ROWS, "row 2"),
        ),
        (
            "ragged-rows",
            "gave a file whose first row lacks its last value",
            _short_row,
            (msg.RAGGED_ROWS, "row 2"),
        ),
        (
            "wrong-separator",
            "gave a file separated by semicolons",
            lambda text: text.replace(",", ";"),
            msg.WRONG_SEPARATOR,
        ),
        (
            "column-names",
            (
                "gave a file with two empty columns at the right, as a spreadsheet "
                "can leave"
            ),
            lambda text: text.replace("\n", ",,\n"),
            (msg.UNNAMED_COLUMNS, "2 columns without a name"),
        ),
    )
]

COLUMN_NAME_CASES = [
    Case(
        "clean",
        "column-names",
        "gave a file with a column name written twice",
        (msg.DUPLICATE_COLUMNS, "named name"),
        **raw_file(patients(JOHN).replace("dob", "name", 1)),
    ),
    Case(
        "clean",
        "column-names",
        "gave a file with spaces after the commas of its header",
        (msg.SPACED_COLUMNS, '" name"'),
        **raw_file(patients(JOHN).replace(",", ", ", 5)),
    ),
]

GUIDES_WROTE = (
    "Wrote outbox/site_a/site_a.guide.html, outbox/site_b/site_b.guide.html, "
    "outbox/linkage/linkage.guide.html, outbox/merge/merge.guide.html, "
    "outbox/host/host.guide.html"
)
TIMES = cast("dict[str, str]", DEFAULT_CONFIGS["guides"]["times"])
CONTACTS = cast("dict[str, dict]", DEFAULT_CONFIGS["guides"]["contacts"])


def _project_text(**changes: object) -> str:
    """Make project.yml text with ``changes``, as guides reads it."""
    project = {**DEFAULT_CONFIGS["bundles"], **changes}
    return yaml.dump(project, sort_keys=False)


GUIDES = [
    *config_cases("guides", missing=None, outside=None, absolute=None),
    Case(
        "guides",
        "config-missing",
        "has no deployment.yml",
        (msg.CONFIG_NOT_FOUND, "deployment.yml"),
        config_text=DROP,
    ),
    *(
        Case("guides", "missing-key", f"left out {what}", says, config={key: DROP})
        for key, what, says in (
            ("contacts", "the contacts", (msg.CONTACTS_INVALID, "it is missing")),
            ("times", "the times", (msg.TIMES_INVALID, "it is missing")),
        )
    ),
    Case(
        "guides",
        "missing-key",
        "left out the destroy time",
        (msg.TIMES_INVALID, "destroy is missing or not a time"),
        config={"times": {k: v for k, v in TIMES.items() if k != "destroy"}},
    ),
    Case(
        "guides",
        "missing-key",
        "left out the host's contact",
        (msg.CONTACTS_INVALID, "host is missing"),
        config={"contacts": {k: v for k, v in CONTACTS.items() if k != "host"}},
    ),
    Case(
        "guides",
        "wrong-type",
        "gave one person for every role",
        (
            msg.CONTACTS_INVALID,
            "coordinator, site_a, site_b, linkage, merge, host is missing",
        ),
        config={"contacts": {"name": "Jo Example", "email": "jo@example.org"}},
    ),
    Case(
        "guides",
        "wrong-type",
        "gave a site's contact as a name alone",
        (msg.CONTACTS_INVALID, "site_a has no valid name and email address"),
        config={"contacts": CONTACTS | {"site_a": "Ana Example"}},
    ),
    Case(
        "guides",
        "wrong-type",
        "gave a contact's email address without an @",
        (msg.CONTACTS_INVALID, "merge has no valid name and email address"),
        config={
            "contacts": CONTACTS
            | {"merge": {"name": "Max Example", "email": "max.example.org"}}
        },
    ),
    Case(
        "guides",
        "deployment",
        "named a contact for a site the project does not have",
        (msg.CONTACTS_INVALID, "it also has site_c"),
        config={
            "contacts": CONTACTS
            | {"site_c": {"name": "Cy Example", "email": "cy@example.org"}}
        },
    ),
    Case(
        "guides",
        "wrong-type",
        "wrote a time's date as mm/dd/yyyy",
        (msg.TIMES_INVALID, "bundles is missing or not a time"),
        config={"times": TIMES | {"bundles": "11/16/2026 09:00"}},
    ),
    Case(
        "guides",
        "wrong-type",
        "gave a date that does not exist (2026-02-30)",
        (msg.TIMES_INVALID, "bundles is missing or not a time"),
        config={"times": TIMES | {"bundles": "2026-02-30 09:00"}},
    ),
    Case(
        "guides",
        "wrong-type",
        "wrote a time as 9am",
        (msg.TIMES_INVALID, "bundles is missing or not a time"),
        config={"times": TIMES | {"bundles": "2026-11-16 9am"}},
    ),
    Case(
        "guides",
        "wrong-type",
        "gave a date with no time",
        (msg.TIMES_INVALID, "bundles is missing or not a time"),
        config={"times": TIMES | {"bundles": "2026-11-16"}},
    ),
    Case(
        "guides",
        "wrong-type",
        "gave the wheel's address over http",
        msg.WHEEL_INVALID,
        config={"wheel": "http://example.org/pprl-3.0.0-py3-none-any.whl"},
    ),
    Case(
        "guides",
        "wrong-type",
        "gave the wheel's SHA-256 too short",
        msg.WHEEL_INVALID,
        config={
            "wheel": "https://example.org/pprl-3.0.0-py3-none-any.whl#sha256=abc123"
        },
    ),
    Case(
        "guides",
        "out-of-range",
        "gave the destroy time before the merge",
        (
            msg.TIMES_OUT_OF_ORDER,
            "destroy (2026-11-16 13:00) before merge (2026-11-16 16:30)",
        ),
        config={"times": TIMES | {"destroy": "2026-11-16 13:00"}},
    ),
    Case(
        "guides",
        "every-choice",
        "gave the address its sites install pprl from",
        GUIDES_WROTE,
        config={"wheel": "https://example.org/pprl/pprl-3.0.0-py3-none-any.whl"},
        accepted=True,
    ),
    Case(
        "guides",
        "every-choice",
        "gave the wheel's address with its SHA-256",
        GUIDES_WROTE,
        config={
            "wheel": "https://example.org/pprl/pprl-3.0.0-py3-none-any.whl#sha256="
            + "0123456789abcdef" * 4
        },
        accepted=True,
    ),
    Case(
        "guides",
        "deployment",
        "added a time of its own (kickoff)",
        (msg.TIMES_INVALID, "it also has kickoff"),
        config={"times": TIMES | {"kickoff": "2026-11-16 08:00"}},
    ),
    Case(
        "guides",
        "project",
        "has no project.yml",
        (msg.CONFIG_NOT_FOUND, "project.yml"),
        files={"project.yml": DROP},
    ),
    Case(
        "guides",
        "project",
        "listed one site in project.yml",
        msg.SITES_NOT_LIST,
        files={"project.yml": _project_text(sites=["site_a"])},
    ),
    Case(
        "guides",
        "output-exists",
        "already has outbox/site_a/site_a.guide.html",
        (msg.GUIDE_EXISTS, "outbox/site_a/site_a.guide.html"),
        files={"outbox/site_a/site_a.guide.html": "an earlier guide\n"},
    ),
    Case(
        "guides",
        "valid",
        "wrote the guides for a project with two sites",
        (GUIDES_WROTE, "and coordinator.guide.html, for you."),
        accepted=True,
        creates="coordinator.guide.html",
    ),
]

CASES = [
    *PPRL,
    *BUNDLES,
    *GUIDES,
    *SYNTH,
    *SELECT,
    *CLEAN,
    *HASH,
    *PACK,
    *LINK,
    *SWEEP,
    *MERGE,
    *INSPECT,
    *NOT_UTF8_CASES,
    *SAVED_CASES,
    *COLUMN_NAME_CASES,
]


def write_doc() -> None:
    """Write the review table of the grid and every case (make docs)."""
    write_text(DOC_PATH, render_markdown(CASES), replace=True)


if __name__ == "__main__":
    write_doc()
