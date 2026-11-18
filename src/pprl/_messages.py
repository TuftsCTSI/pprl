"""Hold every message that pprl can show a user, in one place.

pprl says what it is doing, what it did, and what is wrong. It says each
fact once, and repeats no instruction that the guide already gives. A
problem has a ``text`` (what is wrong) and a ``fix`` (what to do). A result
(an ``[OK]`` line) has no fix.

Rules for every message:

* No patient information. Never show a value from a patient, hash, linkage
  or site data file. Instead, name the file, the column and the study_id
  (study_ids are made by pprl clean, for the study only). Identify rows of
  a file the site exported by their row number, never by the site's own
  id. Counts, column names, site names and measurements (such as the
  secret check's agreement) are fine.
* Never show the secret or a key.
* Name files as they are inside the workspace (``site_a/patients.csv``),
  never by a full path.
* Write settings in backticks (`threshold`), as in the config file.
* Write a count with its noun as the placeholder's format,
  ``{count:row(s)}``, so that no message shows "row(s)" to a user.

Every template (text with {placeholders}) is a ``Message``, which is called
with values for its placeholders. Text with no placeholders is a plain
string. The wording module fills in the templates.

A generated document lists every message, so the wording can be reviewed
there.
"""

from typing import TYPE_CHECKING

from ._wording import ESCALATES, SETTINGS, Message, begin_section

if TYPE_CHECKING:
    from collections.abc import Iterable

# Whose workspace it is, for each role except a site.
_OWNERS = {
    "coordinator": "the coordinator",
    "linkage": "the linkage agent",
    "merge": "the merge agent",
}


def owner(role: str, site: str | None = None) -> str:
    """Say whose workspace it is, for use in a sentence.

    A site with no name given is "a site participant".

    >>> owner("site", "site_a"), owner("site"), owner("linkage")
    ('the site_a participant', 'a site participant', 'the linkage agent')
    """
    if role == "site":
        return f"the {site} participant" if site else "a site participant"
    return _OWNERS[role]


# ---------------------------------------------------------------------------
begin_section("Results and summaries")
# ---------------------------------------------------------------------------

NOTHING_WRITTEN = Message(
    "Nothing was written.",
)
WROTE_HASHES = Message("Wrote {count:hash(es)} from {site} to {output}.")
WROTE_LINKAGES = Message("Found {groups:group(s)} of linked records. Wrote {output}.")
WROTE_LINKAGE_REPORT = Message(
    "Wrote {output}, the linkage report for the coordinator. It holds counts only."
)
# What link found for each pair of sites.
LINKED_PAIR = Message("{first} and {second}: {count:record(s)} linked")
# How strong a pair of sites' linked pairs are, and their near misses, around
# the threshold within the project's review band.
LINK_WEAK = Message(
    "{first} and {second}: {weak:pair(s)} scored {threshold:.2f} to {high:.2f}, just "
    "above the threshold."
)
LINK_NEAR = Message(
    "{site}: about {near:record(s)} not linked to {other} scored {low:.2f} to "
    "{threshold:.2f}, just below the threshold (estimated from {sample:record(s)})."
)
LINK_FALSE = Message(
    "{first} and {second}: roughly {false:false pair(s)} ({low:,} to {high}), from "
    "the records with two or more candidates."
)
FALSE_NO_LIMIT = "no upper limit"
LINK_MOST = Message(
    "{first} and {second} linked {share:.0%} of the records of {smaller}, the "
    "smaller site.",
    "This can be right, if nearly every patient of one site is the other's too.",
    kind=ESCALATES,
)
LINK_MANY_FALSE = Message(
    "{first} and {second}: roughly {share:.0%} of the linked pairs may be false, from "
    "the records with two or more candidates.",
    kind=ESCALATES,
)
LINK_MOSTLY_FALSE = Message(
    "{first} and {second}: about half or more of the linked pairs may be false, from "
    "the records with two or more candidates.",
    kind=ESCALATES,
)
LINK_MOSTLY_WEAK = Message(
    "{first} and {second}: {share:.0%} of the linked pairs scored just above the "
    "threshold.",
    kind=ESCALATES,
)
# Stops link before it compares anything.
LINK_OTHER_SECRET = Message(
    "{first} and {second} hashed with different secrets: how often each bit is set "
    "does not agree (correlation {correlation:.2f}).",
    kind=ESCALATES,
)
NOTHING_LINKED_PAIR = Message(
    "{first} and {second}: no records linked",
    "The linkage file was written, but if these sites share patients, the file "
    "lacks those pairs.",
    kind=ESCALATES,
)
SELECT_TABLE_REPORT = Message("{table}: kept {kept:,} of {rows:row(s)}.")
SELECT_RULE_REPORT = Message("Left out {count:row(s)}: {rule}.")
SELECT_CENSORED = Message("Emptied {columns} in every row kept.")
# A one_of rule that left out unlisted values, and kept no rows for a value it
# does list. This can mean a value was renamed at the site. The message never
# names one of the site's values.
SELECT_VALUES_UNLISTED = Message(
    "{table}: {column} had {values} that its rule does not list, and "
    "no row of {absent}, which it does: a value may have been renamed.",
    "pprl select wrote the rows it kept, without these.",
    kind=ESCALATES,
)
WROTE_SELECTED = Message(
    "Wrote {count:table(s)} to {output}, for {people:patient(s)}.",
)
CLEAN_NOT_SELECTED = Message(
    "Left out {count:row(s)} of {name}, whose {id} is in none of the selected tables.",
)
WROTE_CLEANED = Message(
    "Wrote {kept:row(s)} to {output}, each with a new study_id in place of its "
    "{id}, and {flagged:set-aside row(s)} to {flagged_name}.",
)
MERGE_UNLINKED = Message(
    "Kept {count:record(s)} not in the linkage file, each as a person of their own.",
)
# What merge decided, by table and then by column.
MERGE_SITE_ORDER = Message(
    "Where the sites' values tie, the first site's is kept, in this order: {sites}."
)
MERGE_TABLE_REPORT = Message(
    "{table}: {rows_in:row(s)} from the sites, merged into {rows_out:row(s)}."
)
MERGE_COLUMN_REPORT = Message(
    "{column}: {conflicts:merged row(s)} where the sites differed{how}; "
    "{changed:site value(s)} not kept; {empty:merged row(s)} with no value."
)
# What decided a column's value where the sites differed, shown in brackets.
MERGE_DECIDED = Message("{count:,} by {criterion}")
MERGE_BY_STRATEGY = Message("{strategy} on {column}")
MERGE_BY_SITES = "the order of the sites"
MERGE_TETHERED = Message("each from the row its {lead} came from")
# A check of merge's own counts. Only a fault in pprl can make it fail.
MERGE_INCONSISTENT = Message(
    "pprl's own check of the merged {table} failed: {check}. Nothing was written.",
    kind=ESCALATES,
)
MERGE_CHECKS = {
    "key": Message("two of its rows have the same primary key"),
    "rows": Message("it has more rows than the sites gave"),
    "conflicts": Message("{column} has more rows where the sites differed than rows"),
    "changed": Message("{column} has more values not kept than it can have"),
    "empty": Message("{column} has more rows with no value than rows"),
    "decided": Message(
        "what decided {column} does not add up to its rows where the sites differed"
    ),
}
WROTE_MERGED = Message("Wrote {count:merged table(s)} to the folder {output}.")
WROTE_DATASET = Message(
    "Wrote the dataset to the folder {output}: {individuals:individual(s)} at "
    "{sites:site(s)}.",
)
SWEPT = Message("Linked at {count:threshold(s)}.")
SWEPT_TO_FILE = Message("Linked at {count:threshold(s)}. Wrote {output}.")

# ---------------------------------------------------------------------------
begin_section("Command line")
# ---------------------------------------------------------------------------

UNKNOWN_COMMAND = Message(
    "{program} has no command {command}.",
    "Use one of: {commands}.",
)
MISSING_ARGUMENTS = Message(
    "{command} needs {missing} on the command line.",
    "Write it as {example}.",
)
EXTRA_ARGUMENTS = Message(
    "{command} was given more than it reads: {extra}.",
    "Write it as {example}.",
)
COMMAND_LINE_PROBLEM = Message(
    "pprl could not read the command line ({detail}).",
    "Run pprl alone to see the commands.",
)
DISK_FULL = Message(
    "pprl stopped: the drive is full. Nothing it was writing was kept.",
    "Free some space, then run the command again.",
)
UNEXPECTED = Message(
    "pprl stopped because of an unexpected problem: {kind} in {place} "
    "(pprl {version}).",
    "Nothing it was writing was kept.",
    kind=ESCALATES,
)

# ---------------------------------------------------------------------------
begin_section("Workspace")
# ---------------------------------------------------------------------------


NOT_A_WORKSPACE = Message(
    "This folder is not a pprl workspace.",
    "Open a terminal in your workspace folder, or run pprl init in a new folder "
    "holding your bundle.",
)
MARKER_DAMAGED = Message(
    "pprl-workspace.yml in this folder is damaged.",
    kind=ESCALATES,
)
WORKSPACE_OTHER_RELEASE = Message(
    "This workspace uses pprl {release}, but this is pprl {version}.",
    "Install the release your guide names.",
)

# ---------------------------------------------------------------------------
begin_section("Creating a workspace (pprl init)")
# ---------------------------------------------------------------------------

INIT_NO_BUNDLE = Message(
    "This folder holds no bundle and no project.yml.",
    "Put your bundle here, then run pprl init again.",
)
INIT_SEVERAL_BUNDLES = Message(
    "This folder holds {count:bundle(s)}, and pprl cannot tell which is yours: "
    "{names}.",
    "Remove every bundle but yours, then run pprl init again.",
)
INIT_BUNDLE_UNUSABLE = Message(
    "{name} cannot be used: {reason}.",
    "Download it again, or install the release your guide names.",
)
INIT_CREATED = Message("Created the workspace for {owner} in {project}:")
INIT_COORDINATOR_CREATED = Message(
    "Created the workspace for {owner}, for a new project:"
)
# pprl init in an existing workspace lists the same files, each with its status.
INIT_HERE = Message("This folder is the workspace for {owner} in {project}:")
INIT_COORDINATOR_HERE = Message("This folder is the workspace for {owner}:")
# Each file that init makes: its status, then what it is.
INIT_FILE = Message("{status}: {what}")
INIT_STATUS = {
    "created": Message("created"),
    "kept": Message("kept"),
    "renamed": Message("renamed from {name}"),
    "here": Message("already here"),
}
INIT_WHAT = {
    "bundle": "your role's settings for the project",
    "marker": "marks this folder as a pprl workspace",
    "log": "a record of each run, in plain text",
    "private_key": "unlocks the project's secret, and never leaves this folder",
    "public_key": "your public key, for the coordinator",
}

# ---------------------------------------------------------------------------
begin_section("Destroying a workspace (pprl destroy)")
# ---------------------------------------------------------------------------

DESTROY_HOLDS = Message(
    "This folder, {folder}, the workspace for {owner}, holds {count:file(s)}, "
    "{size} in all:"
)
# Notes on anything unusual about the workspace being destroyed. They are
# shown, and so also recorded in the receipt.
DESTROY_NOTE_MARKER = (
    "pprl-workspace.yml in this folder is damaged, so whose workspace it is "
    "is not known."
)
DESTROY_UNKNOWN_OWNER = "an unknown role"
DESTROY_NOTE_RELEASE = Message(
    "This workspace was created with pprl {release}; this is pprl {version}."
)
# Who receives what each role sends.
RECIPIENTS = {
    "linkage": "the linkage agent",
    "merge": "the merge agent",
    "host": "the host",
    "coordinator": "the coordinator",
    "recipients": "each bundle's recipient",
}
DESTROY_PROMPT = 'To delete these files permanently, type "yes": '
DESTROY_YES = "yes"
DESTROYED = Message("Deleted {count:file(s)} permanently.")
DESTROY_RECEIPT_FAILED = Message(
    "Nothing was deleted: pprl could not write its receipt in this folder.",
    "Check that you can create files in this folder, then run pprl destroy again.",
)
DESTROY_INCOMPLETE = Message(
    "{count:file(s)} in this folder could not be deleted, and are left:",
    "Close any program that has one of them open, then run pprl destroy again.",
)
# What the receipt records about the workspace, besides what this run showed.
DESTROY_RECORD = {
    "workspace": "Workspace:",
    "project": "Project:",
    "created": "Created:",
    "destroyed": "Destroyed:",
}
DESTROY_RECEIPT_NAME = Message("{owner}.receipt_{date}{suffix}.txt")
# The owner in a receipt's name when the marker cannot be read.
DESTROY_NO_OWNER = "workspace"
DESTROY_RECEIPT = Message("Wrote the receipt {name}.")
# The receipt holds this run's record, then the workspace's log.
DESTROY_RECEIPT_UNFINISHED = (
    "pprl destroy began deleting the workspace, and had not finished when "
    "this receipt was written."
)
DESTROY_RECEIPT_LOG = "The workspace's log, as it was before it was deleted:"
DESTROY_RECEIPT_NO_LOG = "The workspace had no log."
DESTROY_DECLINED = Message(
    "Nothing was deleted.",
    'To delete them, run pprl destroy again, and type "yes".',
)
DESTROY_NOT_A_WORKSPACE = Message(
    "This folder is not a pprl workspace, so nothing was deleted.",
    "Open a terminal in your workspace folder, and run pprl destroy there.",
)


# ---------------------------------------------------------------------------
begin_section("Bundles")
# ---------------------------------------------------------------------------

PROJECT_NAME_INVALID = Message(
    "`name` should be the project's name, such as Study 2026, but is {value}.",
    "Write the project's name after `name:` in project.yml.",
)
SITES_NOT_LIST = Message(
    "`sites` should list 2 or more site names, but is {value}.",
    "Write them as a list, such as `sites: [site_a, site_b]`; the first is "
    "the one whose values merge prefers.",
)
SITES_REPEATED = Message(
    "`sites` lists {sites} more than once.",
    "List each site once.",
)
SITES_RESERVED = Message(
    "`sites` lists {sites}, which would name a site's bundle as another file.",
    "Name each site anything but {names}.",
)
# What pprl says about a file or folder it would otherwise overwrite.
_EXISTS = "{name} already exists, and pprl never replaces a file or folder."
BUNDLE_EXISTS = Message(
    _EXISTS,
    "To write new bundles, first delete every earlier one.",
)
WROTE_BUNDLES = Message("Wrote {names}.")
SECRET_EXISTS = Message(
    _EXISTS,
    "To make a new secret, first delete every site's earlier secret file, in "
    "outbox/<site>/.",
)
KEYS_OF_SEVERAL_SETS = Message(
    "The sites' public keys are of different sets of bundles.",
    "Ask each site participant with an earlier bundle to make a new workspace from "
    "the latest one, and to send you the new public key.",
)
WROTE_SECRETS = Message(
    "Locked one new secret to each site's public key. Wrote {names}."
)

# Why a bundle can't be used, as a phrase for a message about that bundle.
BUNDLE_REASONS = {
    "not a bundle": Message("it is not a pprl bundle"),
    "release": Message("it is for pprl {release}, but this is pprl {version}"),
    "other workspace": Message(
        "it is for another project, role or site than this workspace"
    ),
    "replaced": Message(
        "it is from another set of bundles than the one this workspace was created with"
    ),
    "missing": Message("it is not in the workspace"),
}
_PUT_BUNDLE_BACK = (
    "Put the copy you received back in this folder, unchanged, or ask the "
    "coordinator to send it again."
)
BUNDLE_MISSING = Message(
    "The bundle {name} is missing from this workspace.",
    _PUT_BUNDLE_BACK,
)
BUNDLE_UNUSABLE = Message(
    "The bundle {name} cannot be used: {reason}.",
    _PUT_BUNDLE_BACK,
)
# A command whose bundle lacks a setting it needs, such as pprl link run in
# a site's workspace.
SETTING_MISSING = Message(
    "{command} needs the setting `{setting}`, which the bundle {name} does not have.",
    "Run {command} in a workspace whose bundle has it.",
)
SETTING_NO_BUNDLE = Message(
    "{command} needs the setting `{setting}`, and this workspace has no bundle.",
    "Run {command} in a workspace whose bundle has it.",
)

# ---------------------------------------------------------------------------
begin_section("Guides")
# ---------------------------------------------------------------------------

# Text that appears in every guide.
WORKSPACE_DELETED_AT_END = (
    "Every file in this folder is deleted at the end of the project, so keep "
    "nothing else here, such as your notes."
)
# What pprl destroy leaves for each role to do, stated in every guide.
DESTROY_LIMITS = (
    "pprl destroy cannot delete copies kept by your storage (recycle bins, version "
    "history, retention policies and backups; OneDrive keeps deleted files for up "
    "to 93 days), so follow these up. Nor can it "
    "delete files you sent on, such as hashes at the linkage agent: each "
    "recipient destroys the copies received, and confirms that. Your drive's "
    "encryption protects data left on the drive after deletion."
)
CONTACTS_INVALID = Message(
    "`contacts` should give the name and email address of one person for each "
    "of {keys}, but {problem}.",
    "Write one line for each, such as "
    "`site_a: {{name: Ana Example, email: ana@example.org}}`.",
)
# What is wrong with `contacts`, as the ending of CONTACTS_INVALID.
CONTACTS_PROBLEMS = {
    "not contacts": Message("it is {value}"),
    "missing": Message("{keys} is missing"),
    "unknown": Message("it also has {keys}"),
    "invalid": Message("{keys} has no valid name and email address"),
}
TIMES_INVALID = Message(
    "`times` should give each of {keys}, written yyyy-mm-dd HH:MM, but {problem}.",
    "Write each time as yyyy-mm-dd HH:MM, on a 24-hour clock, such as "
    "`destroy: 2026-11-16 17:00`.",
)
# What is wrong with `times`, as the ending of TIMES_INVALID.
TIMES_PROBLEMS = {
    "not times": Message("it is {value}"),
    "wrong": Message("{keys} is missing or not a time"),
    "unknown": Message("it also has {keys}"),
}
TIMES_OUT_OF_ORDER = Message(
    "`times` has {later} ({later_time}) before {earlier} ({earlier_time}).",
    "Give the times in the order the project runs: {keys}.",
)
WHEEL_INVALID = Message(
    "`wheel` should be the https address of pprl's wheel, optionally ending in "
    "#sha256= and the wheel's 64-character SHA-256, but is {value}.",
    "Leave `wheel` out to use the wheel of pprl {release} on GitHub, or give "
    "the address your sites install from.",
)
GUIDE_EXISTS = Message(
    _EXISTS,
    "To write them again, first delete every earlier guide.",
)
WROTE_GUIDES = Message(
    "Wrote {guides}, one for each recipient, and {own}, for you.",
)

# ---------------------------------------------------------------------------
begin_section("Run log")
# ---------------------------------------------------------------------------

# Each run is one section of the workspace's log (such as site_a.log): a
# header, what the terminal showed, and a footer. A section without a
# footer means the run was killed.
LOG_HEADER = Message("===== {time} · pprl {version} · {role} · {command} =====")
LOG_SYSTEM = Message("{system} · Python {python}")
LOG_FOOTER = Message("----- exit code {code} -----")
# Who a workspace is for, as the header says it.
LOG_ROLES = {
    "coordinator": Message("coordinator"),
    "site": Message("{site}"),
    "linkage": Message("linkage agent"),
    "merge": Message("merge agent"),
}

LOG_OPEN_FAILED = Message(
    "pprl could not open its log file, {log} ({reason}). Nothing was done.",
    "Check that you can create and change files in this folder, then run the "
    "command again.",
)
LOG_WRITE_FAILED = Message(
    "pprl stopped because it could not write to its log file, {log} ({reason}).",
    "Nothing it was writing was kept. Free some space, or reconnect the drive, "
    "then run it again.",
)
# Why a log file could not be used, by kind of error. The error's own text is
# never used, because it can quote paths.
LOG_REASONS = {
    "full": "the disk is full",
    "read-only": "the drive is read-only",
    "in use": "the file is open in another program",
    "permission": "permission denied",
    "folder": "it is a folder",
    "unreachable": "the drive could not be reached",
    "other": "the file could not be used",
}
INTERRUPTED = Message(
    "pprl was stopped (Ctrl+C). Nothing it was writing was kept.",
    "Run it again when you are ready.",
)
UNSUPPORTED_PLATFORM = Message(
    "pprl can't run on this computer ({system} on {machine}). It runs on "
    "Linux and Windows with x86-64 processors, and on macOS.",
    "Run pprl on a supported computer. If you can't, contact the coordinator.",
)
LIBRARY_WARNING = Message(
    "A library pprl uses gave a warning ({kind}, in {place}). The run went on.",
    "Nothing is needed: it is in your log, should the coordinator ask for it.",
)

# ---------------------------------------------------------------------------
begin_section("Run log: [INFO] lines (logged, never shown)")
# ---------------------------------------------------------------------------

# The log's [INFO] lines, for troubleshooting. They are written to the
# log only, never shown, each after this tag. They hold measurements,
# versions, and names that pprl and the bundle give things. They never hold
# text the user supplied or anything from a data file.
INFO_TAG = "[INFO]"
INFO_INSTALL = Message("installed: {how}")
# How pprl was installed.
INSTALL_KINDS = {
    "uv tool": Message("with uv tool install"),
    "uv": Message("with uv"),
    "pip": Message("with pip"),
    "other": Message("with {installer}"),
    "unknown": Message("by an unknown installer"),
}
INSTALL_SOURCES = {
    "release wheel": Message("from the release's wheel, {wheel}, on GitHub"),
    "remote wheel": Message("from the wheel {wheel}, at {host}"),
    "local wheel": Message("from the wheel {wheel}, on this computer"),
    "editable": Message("from source, editable"),
    "source": Message("from source"),
    "index": Message("from a package index"),
}
INFO_PACKAGES = Message("packages: {packages}")
INFO_COMPUTER = Message("computer: {cpus:CPU(s)} · {memory} memory")
INFO_INPUT_CSV = Message(
    "read {name}: {size} · {rows:row(s)} · {columns:column(s)} · {encoding} · "
    "{endings} line endings"
)
INFO_INPUT_PPRL = Message(
    "read {name}: {size} · {kind} · pprl {release} · file {id} · bundle set "
    "{set} · {members}{inputs}"
)
INFO_MEMBER = Message("{name} {rows:row(s)} × {columns:column(s)}")
INFO_FROM = Message(" · made from {files}")
INFO_FROM_FILE = Message("{kind} {id}")
INFO_STEP = Message("step: {step} · {seconds:.2f} s")
INFO_CRASH = Message("crash: {chain}")
INFO_CRASH_FRAME = Message("{file}, line {line}, in {function}")
INFO_CRASH_CAUSE = Message(" · raised while handling {kind}")
INFO_OTHER_BUNDLES = Message("other bundles here: {names}")
INFO_FINISHED = Message("finished: {seconds:.2f} s · peak memory {memory}")
INFO_LINK_SECRET = Message(
    "link same secret, {first} and {second}: bit correlation {correlation}"
)
INFO_LINK_PAIR = Message(
    "link pair {first} and {second}: {linked:,} linked, of {first_records:,} and "
    "{second_records:,} records"
)
INFO_LINK_BEST = Message("link best scores of every site's sample, by 0.01: {counts}")
INFO_LINK_WARNING = Message("link warning: {text}")
INFO_LINK_BAND = Message("link threshold {threshold}, review band {low} to {high}")
INFO_LINK_FALSE = Message(
    "link false pairs of {first} and {second}: {estimate}, from {low} to {high}; "
    "{several} records with several candidates, of {candidates} with any"
)
INFO_MERGE_PART = Message(
    "merged {table}{part}: {rows_in:row(s)} in, {rows_out:row(s)} out"
)
INFO_MERGE_COLUMN = Message(
    "merged {table}{part}, {column}: {conflicts:,} differed{how}, {changed:,} "
    "not kept, {empty:,} with no value"
)
INFO_PARTITION = Message(", partition {value}")
INFO_UNKNOWN = "unknown"
# The bit correlation in the log when the bundle turned the same-secret check off.
INFO_OFF = "not checked"
ENCODINGS = {
    "utf-8": "UTF-8",
    "utf-8-sig": "UTF-8 with a byte-order mark",
    "cp1252": "Windows text, read as such",
}
LINE_ENDINGS = {
    "\r\n": "Windows",
    "\n": "Unix",
    "\r": "classic Mac",
    "mixed": "mixed",
    "none": "no",
}

# ---------------------------------------------------------------------------
begin_section("Config files")
# ---------------------------------------------------------------------------

_USE_GIVEN_CONFIG = (
    "Write each setting on a line of its own, as its name, a colon and its "
    "value, such as `threshold: 0.80`."
)

CONFIG_NOT_FOUND = Message(
    "Cannot find the config file {name} in this workspace.",
    "Put it in this workspace, with exactly that name.",
    kind=SETTINGS,
)
CONFIG_EMPTY = Message(
    "The config file {name} is empty.", _USE_GIVEN_CONFIG, kind=SETTINGS
)
CONFIG_NOT_YAML = Message(
    "The config file {name} cannot be read: its layout is wrong near line "
    "{line}, column {column}.",
    "Correct that line; each setting is one line, such as `threshold: 0.80`.",
    kind=SETTINGS,
)
CONFIG_NOT_MAPPING = Message(
    "The config file {name} does not hold settings.",
    _USE_GIVEN_CONFIG,
    kind=SETTINGS,
)
CONFIG_UNKNOWN_KEYS = Message(
    "The config file {name} has the {keys:setting(s)}, which this command does "
    "not read.",
    "Remove them, or correct their spelling. The settings it reads are: {allowed}.",
    kind=SETTINGS,
)
CONFIG_RESERVED_KEYS = Message(
    "The config file {name} sets {keys}, which only pprl itself may set.",
    "Remove those lines from {name}.",
    kind=SETTINGS,
)
CONFIG_MISSING_KEY = Message(
    "The config file has no `{key}` setting.",
    "Add `{key}:`.",
    kind=SETTINGS,
)
CONFIG_KEY_NOT_A_NAME = Message(
    "`{key}` should be one file name, but is {value}.",
    "Write a single file name, such as `{key}: site_a.csv`.",
    kind=SETTINGS,
)
CONFIG_NOT_TRUE_FALSE = Message(
    "`{key}` should be true or false, but is {value}.",
    "Write `{key}: true` or `{key}: false`.",
    kind=SETTINGS,
)

# ---------------------------------------------------------------------------
begin_section("Files and folders")
# ---------------------------------------------------------------------------

NAME_OUTSIDE = Message(
    "{what} is outside the workspace ({name}), and pprl reads and writes files "
    "only inside it.",
    "Move the file into the workspace, and give its name there, such as "
    "site_a/patients.csv.",
)
FILE_NOT_FOUND = Message(
    "Cannot find the {what} {name} in this workspace.",
    "Put it in this workspace, with exactly that name.",
)
OUTPUT_EXISTS = Message(
    _EXISTS,
    "Move or delete the earlier {name}.",
)
FILE_EMPTY = Message(
    "{name} is empty (0 bytes).",
    "Check that it is the right file, saved completely.",
)
FILE_NO_ROWS = Message(
    "{name} has column names but no rows.",
    "Check that it is the right file, exported completely.",
)
FILE_MALFORMED = Message(
    "{name} is not a well-formed CSV file{near}.",
    "Export it again, and save it as CSV UTF-8.",
)
FILE_NOT_UTF8 = Message(
    "{name} is not saved as UTF-8 text.",
    "Save it again as CSV UTF-8 (in Excel: File > Save As > CSV UTF-8).",
)
BLANK_ROWS = Message(
    "{name} has empty rows before its last row: {rows:row(s)} (the header is row 1).",
    "Delete the empty rows, or check that the file was exported completely.",
)
RAGGED_ROWS = Message(
    "{name} has {count:row(s)} without {columns} values, as its header has: "
    "{rows:row(s)} (the header is row 1).",
    "Export it again: those rows have a missing or extra comma, or were cut off.",
)
WRONG_SEPARATOR = Message(
    "{name} separates its values with semicolons or tabs, not commas.",
    "Save it again as CSV, with commas (in Excel: File > Save As > CSV UTF-8).",
)
UNNAMED_COLUMNS = Message(
    "{name} has {count:column(s)} without a name, often empty columns left at "
    "the right of a spreadsheet.",
    "Delete those columns, then save the file again.",
)
SPACED_COLUMNS = Message(
    "{name} has spaces around the {columns:column name(s)}.",
    "Remove the spaces from the first row of the file.",
)
DUPLICATE_COLUMNS = Message(
    "{name} has more than one column named {columns}.",
    "Keep one column of each name.",
)

# ---------------------------------------------------------------------------
begin_section("pprl files")
# ---------------------------------------------------------------------------

# What each kind of pprl file is called in messages.
FILE_KINDS = {
    "bundle": "bundle",
    "private_key": "private key file",
    "public_key": "public key file",
    "secret": "secret file",
    "selected": "selected tables file",
    "clean": "cleaned patient file",
    "hashes": "hash file",
    "tables": "packed tables file",
    "linkages": "linkage file",
    "linkage_report": "linkage report",
}
_SEND_AGAIN = (
    "If you received it, download it again, and use it without opening it. If pprl "
    "wrote it here, delete it, and run again the command that writes it."
)
NOT_A_PPRL_FILE = Message(
    "{name} is not a pprl file.",
    _SEND_AGAIN,
)
FILE_DAMAGED = Message(
    "{name} is damaged: it is not whole, or not as pprl wrote it.",
    _SEND_AGAIN,
)
FILE_IS_BUNDLE = Message(
    "{name} is a bundle, not the file your guide names.",
    "Delete it from this workspace, and wherever you shared it, now.",
    kind=ESCALATES,
)
FILE_OTHER_KIND = Message(
    "{name} is a {found}, not a {expected}.",
    "Use the file your guide names, saved under that name.",
)
FILE_OTHER_RELEASE = Message(
    "{name} was written by pprl {release}, but this is pprl {version}.",
    kind=ESCALATES,
)
FILE_OTHER_PROJECT = Message(
    "{name} is from another project.",
    "Use the file sent for this project.",
)
FILE_OTHER_SET = Message(
    "{name} was made with other bundles than this workspace's: an earlier "
    "set, or another project's.",
    kind=ESCALATES,
)
FILE_OF_OTHER_SITE = Message(
    "{name} holds the records of {found}, not {site}.",
    "Use {site}'s own file, saved under the name your guide gives.",
)
FILE_OTHER_RUN = Message(
    "{name} and the linkage file come from different runs of pprl clean at "
    "{site}: the study_ids would join the wrong patients' records.",
    kind=ESCALATES,
)

# ---------------------------------------------------------------------------
begin_section("File contents")
# ---------------------------------------------------------------------------

# The fix for a problem in a patient file that pprl clean would have repaired
# or set aside.
_RUN_CLEAN = (
    "Run pprl clean on the original patient file: it repairs what it can and sets "
    "the rest aside."
)

MISSING_COLUMNS = Message(
    "{name} is missing the {columns:column(s)}. Its columns are: {found}.",
    "Make the first row of the file match the column names in your guide, capitals "
    "included.",
)
UNEXPECTED_COLUMNS = Message(
    "{name} has the {columns:column(s)}, which pprl does not use.",
    "Remove them from the file.",
)
_USE_AS_WRITTEN = "Use the file pprl wrote, unchanged."
DUPLICATE_IDS = Message(
    "{name} uses {count:value(s)} of {column} on more than one row: {rows:row(s)}.",
    _USE_AS_WRITTEN,
)
INVALID_SITE = Message(
    'The site name "{site}" cannot be used: a site name may contain only '
    "letters, digits and underscores.",
    "Use the site name your guide gives.",
    kind=SETTINGS,
)
BAD_FORMAT = Message(
    "The {column} column of {name} has {count:value(s)} not {format}: {rows:row(s)}.",
    _RUN_CLEAN + " {how}",
)
FORMATS = {
    "date": (
        "dates written yyyy-mm-dd",
        (
            "Dates are written yyyy-mm-dd, such as 1990-03-15; a spreadsheet may "
            "have changed their format when the file was saved."
        ),
    ),
    "zip": (
        "5-digit ZIP codes",
        (
            "ZIP codes are 5 digits; a spreadsheet may have dropped a leading zero "
            "(2101 for 02101)."
        ),
    ),
    "state": (
        "2-letter state codes in capitals",
        "States are 2 capital letters, such as MA.",
    ),
}
IDS_INVALID = Message(
    "{name} has {count:row(s)} whose {column} is empty, or has spaces around "
    "it: {rows:row(s)} (the header is row 1).",
    _USE_AS_WRITTEN,
)
NO_IDENTIFIERS = Message(
    "{name} has {count:row(s)} with no identifying value at all (every field "
    "is empty): {rows:row(s)}.",
    _RUN_CLEAN,
)

# ---------------------------------------------------------------------------
begin_section("Hashing: secret, fields and hashing settings")
# ---------------------------------------------------------------------------

_COPY_FIELDS = (
    "Write each field to hash on a line of its own under `fields`, with its "
    "weight, such as `name: 0.45`, or with its weight and type, such as "
    "`dob: {{weight: 0.25, type: date}}`."
)
_NEW_BUNDLE = "Ask the coordinator for a new bundle."

SECRET_OTHER_KEY = Message(
    "{name} was not locked to this workspace's public key.",
    "Send outbox/coordinator/{site}.public_key.pprl to the coordinator over a "
    "secure channel, and ask for a new secret file.",
)
FIELDS_MISSING = Message(
    "The config file has no `fields`.", _COPY_FIELDS, kind=SETTINGS
)
_USE_ID = (
    "Set `id` to the name of the column of each site's patient file and "
    "tables that holds the site's own id for each patient, such as `id: person_id`."
)
ID_COLUMN_MISSING = Message("The config file has no `id`.", _USE_ID, kind=SETTINGS)
ID_COLUMN_INVALID = Message(
    "`id` should be the name of a column, without spaces around it, but is {value}.",
    _USE_ID,
    kind=SETTINGS,
)
ID_COLUMN_RESERVED = Message(
    "`id` is {value}, a name pprl gives a column of its own.",
    "Name the column the site's export uses, such as person_id. " + _USE_ID,
    kind=SETTINGS,
)
ID_COLUMN_IS_FIELD = Message(
    "`id` is {value}, which is also one of `fields`.",
    "The id is never hashed: " + _USE_ID,
    kind=SETTINGS,
)
FIELDS_NOT_SETTINGS = Message(
    "`fields` should be a block of fields to hash, each with its weight, such "
    "as `name: 0.5` or `dob: {{weight: 0.25, type: date}}`, but is {value}.",
    _COPY_FIELDS,
    kind=SETTINGS,
)
FIELD_NO_WEIGHT = Message(
    "The field {field} in `fields` has no `weight`.",
    "Give it a weight above 0, such as `{field}: 0.45`, or "
    "`{field}: {{weight: 0.25, type: date}}` for a field that is not text.",
    kind=SETTINGS,
)
FIELD_UNKNOWN_KEYS = Message(
    "The field {field} in `fields` has the {keys:setting(s)}, which pprl does "
    "not read. A field may have weight and type.",
    _COPY_FIELDS,
    kind=SETTINGS,
)
FIELD_BAD_TYPE = Message(
    "The field {field} in `fields` has the type {value}, but the types are {types}.",
    _COPY_FIELDS,
    kind=SETTINGS,
)
FIELD_BAD_WEIGHT = Message(
    "The weight of the field {field} should be a number above 0, but is {value}.",
    _COPY_FIELDS,
    kind=SETTINGS,
)

# ---------------------------------------------------------------------------
begin_section("Linkage")
# ---------------------------------------------------------------------------

HASHES_NOT_LIST = Message(
    "`hashes` should list 2 or more hash files, but is {value}.",
    "List every site's hash file, such as "
    "`hashes: [site_a.hashes.pprl, site_b.hashes.pprl]`.",
    kind=SETTINGS,
)
THRESHOLD_INVALID = Message(
    "`threshold` should be a number from 0.0 to 1.0, but is {value}.",
    "Give a number such as `threshold: 0.80`.",
    kind=SETTINGS,
)
HASH_LENGTH_INVALID = Message(
    "`hash_length` should be a whole number of bits, a multiple of 8, from {lo} "
    "to {hi} and at least 8 for each field, but is {value}.",
    "Give the number of bits the project chose with pprl sweep, such as "
    "`hash_length: 1024`.",
    kind=SETTINGS,
)
FILL_RATIO_INVALID = Message(
    "`fill_ratio` should be a number above 0, up to {hi}, but is {value}.",
    "Give the fill ratio the project chose with pprl sweep, such as `fill_ratio: 1.0`.",
    kind=SETTINGS,
)
XOR_FOLDS_INVALID = Message(
    "`xor_folds` should be a whole number of 0 or more, but is {value}.",
    "Remove the line to leave hashes unfolded, or give a number such as "
    "`xor_folds: 1`.",
    kind=SETTINGS,
)
PROJECT_SALT_INVALID = Message(
    "`project_salt` should be text, but is {value}.",
    "Remove the line to hash without one, or write it as text, in quotes if it "
    "holds only digits.",
    kind=SETTINGS,
)
SECRET_CHECK_INVALID = Message(
    "`secret_check` should be true or false, but is {value}.",
    "Write `secret_check: true`, or `secret_check: false` if the project uses "
    "`record_salt`.",
    kind=SETTINGS,
)
RECORD_SALT_INVALID = Message(
    "`record_salt` should be a block with a `field`, and perhaps a `length`, a "
    "whole number of 1 or more, but is {value}.",
    "Remove the lines to hash without one, or write it as "
    "`record_salt: {{field: zip, length: 3}}`.",
    kind=SETTINGS,
)
RECORD_SALT_NOT_A_FIELD = Message(
    "The `field` of `record_salt` is {value}, which is not one of `fields`.",
    "Name one of the fields to hash.",
    kind=SETTINGS,
)
WORKERS_INVALID = Message(
    "`workers` should be a whole number of 1 or more, but is {value}.",
    "Remove the line to use every processor, or give a number such as `workers: 4`.",
    kind=SETTINGS,
)
DUPLICATE_SITES = Message(
    "More than one hash file comes from {sites}.",
    kind=ESCALATES,
)
HASHES_UNREADABLE = Message(
    "{name} has hashes (the hash column) that cannot be read.",
    kind=ESCALATES,
)
HASH_LENGTH_MISMATCH = Message(
    "The hash files have different lengths: {details}.",
    kind=ESCALATES,
)

# ---------------------------------------------------------------------------
begin_section("Threshold and weight sweeps")
# ---------------------------------------------------------------------------

THRESHOLDS_INVALID = Message(
    "`thresholds` should be a list of numbers from 0.0 to 1.0, but is {value}.",
    "Write it as a list, such as `thresholds: [0.9, 0.95, 0.975]`.",
)
SWEEP_MISSING_KEY = Message(
    "The config file has no `{key}` setting, which a sweep over {mode} needs.",
    "Add `{key}`: a sweep over patient files needs `fields`, written as in "
    "project.yml, and `assessment: true` or `assessment: false`.",
)
SWEEP_PATIENTS_NOT_LIST = Message(
    "`patients` should list 2 or more patient files, but is {value}.",
    "List the patient files, such as "
    "`patients: [site_a/patients.csv, site_b/patients.csv]`.",
)
SWEEP_NO_ASSESSMENT = Message(
    "`assessment` needs patient files: hash files have study_ids, which pprl "
    "clean draws anew at each site, so nothing tells which records are the "
    "same person.",
    "Remove `assessment`, or sweep the patient files synth wrote instead.",
)
SWEEP_CONFIG_DONE = Message("Configuration {index}/{total} ({label}): done")
SWEEP_CONFIG_BEST_F1 = Message(
    "Configuration {index}/{total} ({label}): best F1 {f1:.2%}"
)
SWEEP_BEST = Message(
    "Best: F1 {f1:.2%} (precision {precision:.2%}, recall {recall:.2%}) at {settings}",
)
SWEEP_SUGGESTED = Message("Suggested: {settings} ({matches:match(es)})")
SWEEP_TABLE_HEADINGS = ("Threshold", "Matches", "Precision", "Recall")

# ---------------------------------------------------------------------------
begin_section("Cleaning")
# ---------------------------------------------------------------------------

CLEAN_CONVERTED = Message(
    "{name} was not saved as UTF-8 text, so it was read as Windows text "
    "(Windows-1252).",
)
CLEAN_REPAIRED = Message(
    "In the {column} column, {what} in {count:row(s)} of {name}: {rows:row(s)} "
    "(the header is row 1).",
)
CLEAN_NO_PATIENT = Message(
    "Found no row in {name} for {count:patient(s)} in the selected tables, so pprl "
    "pack will leave out their rows.",
    "Check that {name} and the tables were exported together.",
)
CLEAN_SET_ASIDE = Message(
    "Set aside {count:row(s)} of {name}, because {reason}: {rows:row(s)} (the "
    "header is row 1).",
)
CLEAN_SET_ASIDE_FILE = Message(
    "{flagged} lists each row set aside, and why.",
    "Correct them in your own records and export {name} again, or leave them out.",
)
# What clean repaired, for CLEAN_REPAIRED.
REPAIRS = {
    "spaces": "removed spaces around the value",
    "time": "removed a time after the date",
    "zip_plus_4": "shortened a ZIP+4 code to its first 5 digits",
    "state_case": "wrote the state code in capitals",
}
# Why clean set a row aside. Used in CLEAN_SET_ASIDE and in the flagged
# file's `reason` column.
REASONS = {
    "id_empty": Message("its {column} is empty"),
    "id_repeated": Message("its {column} is also used on another row"),
    "no_identifiers": Message("every identifying value is empty"),
    "date": Message("its {column} is not a date written yyyy-mm-dd"),
    "zip_short": Message(
        "its {column} has 4 digits, so it may have lost a leading zero"
    ),
    "zip": Message("its {column} is not a 5-digit ZIP code"),
    "state": Message("its {column} is not a 2-letter state code"),
}
# The columns of the set-aside file, besides the site's id column.
ROW_COLUMN = "row"
REASON_COLUMN = "reason"
REASON_SEPARATOR = "; "

# ---------------------------------------------------------------------------
begin_section("Selecting a site's rows")
# ---------------------------------------------------------------------------

_SELECT_RULES = (
    "Write each rule on a line of its own under `keep`, as one of "
    '`- not_empty: score`, `- one_of: {{year: ["2023", "2024"]}}` or '
    "`- found_in: {{table: visits, on: [person_id]}}`."
)
SITE_TABLES_INVALID = Message(
    "`tables` in the bundle should give each table's columns, primary key and rules.",
    _NEW_BUNDLE,
    kind=SETTINGS,
)
SITE_TABLES_NO_ID = Message(
    "`tables` in the bundle has the {tables:table(s)} without the column "
    "{column} in its primary key.",
    _NEW_BUNDLE,
    kind=SETTINGS,
)
KEEP_INVALID = Message(
    "`keep` of the table {table} should be a list of rules, but is {value}.",
    _SELECT_RULES,
    kind=SETTINGS,
)
RULE_INVALID = Message(
    "Rule {n} of the table {table}'s `keep` {problem}.",
    _SELECT_RULES,
    kind=SETTINGS,
)
# What is wrong with a rule, for RULE_INVALID.
RULE_PROBLEMS = {
    "form": Message(
        "should be one of `not_empty`, `one_of` or `found_in`, with its settings"
    ),
    "column": Message("names {column}, which is not a column of {table}"),
    "one_of": Message(
        "should give one column, with a list of its values, each in quotes"
    ),
    "found_in": Message(
        "should give the `table` to look in, and the columns `on` which "
        "to match its rows, {id} among them"
    ),
    "table": Message("looks in {other}, which is not another of the project's tables"),
    "on": Message("matches on {column}, which {other} does not have"),
}
RULES_CIRCULAR = Message(
    "The `found_in` rules of {tables} look in each other's tables, in a loop.",
    "Let each table look only in tables that do not look back in it.",
    kind=SETTINGS,
)
CENSOR_INVALID = Message(
    "`censor` of the table {table} should list some of its columns, none in "
    "its `primary_key`, but is {value}.",
    "List the columns to empty, such as `censor: [notes, comments]`.",
    kind=SETTINGS,
)
SELECT_TABLE_MISSING = Message(
    "Cannot find {name} in this workspace.",
    "Save each table your guide names in the folder {folder}, with that name.",
)
SELECT_KEY_REPEATED = Message(
    "{count:row(s)} of {name} that the project's rules keep share their "
    "{columns} with another: {rows:row(s)} (the header is row 1).",
    "Correct the export, then run pprl select again.",
)
# What select left out, by rule, for SELECT_RULE_REPORT.
RULE_DESCRIPTIONS = {
    "not_empty": Message("{column} is empty"),
    "one_of": Message("{column} is not one of {count:listed value(s)}"),
    "found_in": Message("{table} has no row with the same {columns}"),
}

# ---------------------------------------------------------------------------
begin_section("Packing a site's tables")
# ---------------------------------------------------------------------------

PACK_LEFT_OUT = Message(
    "Left out {count:row(s)} of the table {table}, whose patients have no "
    "study_id: pprl clean set them aside, or they are not in {patients}.",
    "{flagged} lists the patients set aside and why. Correct them in {patients} and "
    "run pprl clean again, or leave them out.",
)
PACK_OTHER_SELECTION = Message(
    "{cleaned} was not made from {selected}: pprl select ran again after pprl clean.",
    kind=ESCALATES,
)
PACK_NOT_SELECTED = Message(
    "{count:row(s)} of the table {table} in {name} do not meet the project's "
    "rules for it, so pprl select did not write them.",
    kind=ESCALATES,
)
WROTE_TABLES = Message(
    "Wrote {count:table(s)} to {output}, with study_ids in place of {id}."
)

# ---------------------------------------------------------------------------
begin_section("Merging")
# ---------------------------------------------------------------------------

_MERGE_RULES = (
    "Give each table its `primary_key`, and under `column` a rule for each "
    "other column: `rank_by`, a `tether` or `drop: true`, such as "
    "`score: {{rank_by: [{{take_highest: {{column: score}}}}]}}`."
)

MERGE_SITES_NOT_LIST = Message(
    "`sites` should list the sites to merge, such as [site_a, site_b], but is {value}.",
    "List each site's folder name, in the order that breaks ties.",
    kind=SETTINGS,
)
TABLES_NOT_SETTINGS = Message(
    "`tables` should describe each table to merge, but is {value}.",
    _MERGE_RULES,
    kind=SETTINGS,
)
TABLE_NAMES_INVALID = Message(
    "Table names may contain only letters, digits and underscores: {names}.",
    "Use each table's file name without .csv.",
    kind=SETTINGS,
)
SITE_TABLE_MISSING = Message(
    "{name} has no table {table}.",
    kind=ESCALATES,
)
TABLE_NOT_SETTINGS = Message(
    "The table {table} should be a block of settings.", _MERGE_RULES, kind=SETTINGS
)
TABLE_UNKNOWN_KEYS = Message(
    "The table {table} has the {keys:setting(s)}, which pprl does not read. A "
    "table may have: {allowed}.",
    "Remove them, or correct their spelling.",
    kind=SETTINGS,
)
PRIMARY_KEY_INVALID = Message(
    "The table {table} needs a `primary_key` list that includes {column}, the "
    "project's `id`.",
    "Add it, such as `primary_key: [{column}]`.",
    kind=SETTINGS,
)
PRIMARY_KEY_NOT_IN_DATA = Message(
    "`primary_key` of the table {table} names the {columns:column(s)}, which "
    "the data does not have.",
    "Correct the column names, or check the site files.",
    kind=SETTINGS,
)
COLUMN_AND_PARTITIONS = Message(
    "The table {table} has both `column` and `partition_by`/`partitions`.",
    "Use `column` for one set of rules, or `partition_by` with `partitions` "
    "for rules by partition, but not both.",
    kind=SETTINGS,
)
PARTITION_BY_NOT_IN_KEY = Message(
    "`partition_by` of the table {table} is {column}, which is not in `primary_key`.",
    "Add {column} to `primary_key`, or partition by a column in it.",
    kind=SETTINGS,
)
PARTITIONS_MISSING = Message(
    "The table {table} has `partition_by`, but no `partitions`.",
    "Add a `partitions` block with the rules for each partition. " + _MERGE_RULES,
    kind=SETTINGS,
)
PARTITION_VALUES_EMPTY = Message(
    "The table {table} has {count:row(s)} with an empty {column}, in no partition.",
    "Fill in {column} in the site files.",
    kind=SETTINGS,
)
PARTITIONS_UNDECLARED = Message(
    "The table {table} has {count:value(s)} of {column} with no entry under "
    "`partitions`.",
    "Add an entry under `partitions` for every {column} value in the data.",
    kind=SETTINGS,
)
COLUMNS_NOT_SETTINGS = Message(
    "`column` of the table {table} should give each column its rule.",
    _MERGE_RULES,
    kind=SETTINGS,
)
COLUMNS_UNDECLARED = Message(
    "The table {table} has the {columns:column(s)} without a rule under `column`.",
    "Give each a rule: `rank_by`, a `tether`, or `drop: true`.",
    kind=SETTINGS,
)
COLUMNS_NOT_IN_DATA = Message(
    "The table {table} has rules for the {columns:column(s)}, which the data "
    "does not have.",
    "Remove those rules, or check the site files.",
    kind=SETTINGS,
)
COLUMN_RULE_NOT_SETTINGS = Message(
    "The rule for {column} should be a block of settings, such as `{{drop: true}}`.",
    _MERGE_RULES,
    kind=SETTINGS,
)
COLUMN_RULE_UNKNOWN_KEYS = Message(
    "The rule for {column} has the {keys:setting(s)}, which pprl does not read. "
    "A rule may have: {allowed}.",
    "Remove them, or correct their spelling.",
    kind=SETTINGS,
)
COLUMN_NO_RULE = Message(
    "The rule for {column} says neither how to choose between sites nor to "
    "drop the column.",
    "Give it `rank_by`, a `tether`, or `drop: true`.",
    kind=SETTINGS,
)
RANK_BY_NOT_LIST = Message(
    "`rank_by` for {column} should be a list of strategies, each with its settings.",
    "Write each strategy on its own line under `rank_by`, such as "
    "`- take_highest: {{column: score}}`.",
    kind=SETTINGS,
)
RANK_BY_ITEM = Message(
    "Item {position} of `rank_by` for {column} should name one strategy, "
    "with its settings.",
    "Write it like `take_highest: {{column: score}}`.",
    kind=SETTINGS,
)
STRATEGY_UNKNOWN = Message(
    "The rule for {column} uses the strategy {strategy}, which pprl does not have.",
    "Use one of: {strategies}.",
    kind=SETTINGS,
)
STRATEGY_SETTINGS = Message(
    "{strategy} in the rule for {column} needs exactly these settings: {settings}.",
    "Add any that are missing, and remove any others.",
    kind=SETTINGS,
)
STRATEGY_NO_SETTINGS = Message(
    "{strategy} in the rule for {column} takes no settings.",
    "Write it as `{strategy}: {{}}`.",
    kind=SETTINGS,
)
RANK_COLUMN_NOT_IN_DATA = Message(
    "{strategy} in the rule for {column} compares {compared}, which the data "
    "does not have.",
    "Name a column of the table.",
    kind=SETTINGS,
)
TETHER_UNKNOWN = Message(
    "{column} is tethered to {target}, which has no rule.",
    "Tether it to a column that has `rank_by`.",
    kind=SETTINGS,
)
TETHER_TARGET_NO_STRATEGY = Message(
    "{column} is tethered to {target}, which has no `rank_by`.",
    "Tether it to a column that has `rank_by`.",
    kind=SETTINGS,
)
TETHER_WITH_STRATEGY = Message(
    "{column} has both a `tether` and `rank_by`.",
    "Keep one: a tethered column takes its value from the same row as the "
    "column it is tethered to.",
    kind=SETTINGS,
)
TETHER_CIRCULAR = Message(
    "The tethers of {column} lead back to {column}.",
    "Tether columns to a column with `rank_by`.",
    kind=SETTINGS,
)
VALUES_NOT_NUMBERS = Message(
    "take_highest needs numbers, but {column} has {count:value(s)} other than "
    "numbers, in {where}.",
    "Correct those values, or use another strategy.",
    kind=SETTINGS,
)
VALUES_NOT_DATES = Message(
    "{strategy} needs dates, but {column} has {count:value(s)} other than "
    "dates written yyyy-mm-dd, in {where}.",
    "Write dates as yyyy-mm-dd (optionally with a time, HH:MM:SS) in the site files.",
    kind=SETTINGS,
)
SCALE_MISSING = Message(
    "take_top_ordinal in the rule for {column} needs `scale`: a list of its "
    "values, lowest first.",
    "Write it like `scale: [low, medium, high]`.",
    kind=SETTINGS,
)
VALUES_NOT_IN_SCALE = Message(
    "{column} has {count:value(s)} not on the `scale` of take_top_ordinal, in {where}.",
    "Add every value the data uses to `scale`, or correct the data.",
    kind=SETTINGS,
)
# Where the values reported by VALUES_NOT_NUMBERS and the messages above are.
ROWS_IN_TABLE = Message("{name}, table {table}")

# ---------------------------------------------------------------------------
begin_section("Synthetic data scenarios")
# ---------------------------------------------------------------------------

# Scenario messages may show scenario values, because a scenario holds only
# synthetic data. Each message starts with the value's location in the YAML,
# such as ``individuals[3].condition[1]: missing `start_date```.

SCENARIO_NOT_FOUND = Message(
    "Cannot find the scenario {name} in this workspace.",
    "Place the scenario file or folder in this workspace, and check its name.",
)
SCENARIO_NOT_YAML = Message(
    "not valid YAML near line {line}, column {column}",
    "Check the layout of that line.",
)
SCENARIO_NOT_MAPPING = Message(
    "expected keys such as `seed:` and `sites:`, not a list or a single value",
    "Write the scenario as keys and values, one to a line, such as `seed: 42`.",
)
SCENARIO_KEY_REPEATED = Message(
    "already given in an earlier file",
    "Give a key other than a list in one file only.",
)
SCENARIO_FILE_UNNUMBERED = Message(
    "the name must start with a number that sets its load order",
    "Rename it, such as 01_preamble.yml.",
)
SCENARIO_UNKNOWN_KEY = Message(
    "not an OMOP CDM v5.4 table or column, or a synth key",
    "Correct the spelling.",
)
SCENARIO_NOT_MAPPING_VALUE = Message(
    "expected keys and values",
    "Write it as keys and values, one to a line, such as `name: value`.",
)
SCENARIO_NOT_LIST = Message(
    "expected a list", "Write each item on a line of its own, starting with `- `."
)
SCENARIO_KEY_NOT_ALLOWED = Message("unknown key", "Use one of: {allowed}.")
SCENARIO_MISSING = Message("missing `{key}`", "Add `{key}`.")
SCENARIO_RATE = Message(
    "must be a chance from 0 to 1, not {value}",
    "Give a number from 0 to 1.",
)
SCENARIO_WRONG_TYPE = Message("cannot read {value} as {kind}", "Write {kind}.")
# What a scenario value must be, keyed by Python type name.
SCENARIO_TYPES = {
    "int": "an integer",
    "float": "a number",
    "str": "text",
    "bool": "true or false",
    "date": "a date (yyyy-mm-dd)",
    "datetime": "a datetime (yyyy-mm-dd HH:MM:SS)",
}
SCENARIO_UNKNOWN_NAME = Message(
    "unknown {table} {value}",
    "Name a {table} the scenario lists, or give its id number.",
)
SCENARIO_SHORT_COLUMN_NAME = Message(
    "write `{short}` (column names leave off the table name)",
    "Rename the key to `{short}`.",
)
SCENARIO_SHORT_TABLE_NAME = Message(
    "write `{short}` for the {table} table",
    "Rename the key to `{short}`.",
)
SCENARIO_RESERVED_KEYS = Message(
    "only pprl itself may set these keys",
    "Remove them from the scenario.",
)
SCENARIO_PERSON_ROWS_AT_TOP = Message(
    "{key} rows belong to an individual",
    "List them under an individual in `individuals`.",
)
SCENARIO_BAD_SEED = Message(
    "must be an integer or text, not {value}", "Give a seed such as 2026."
)
SCENARIO_NO_SITES = Message(
    "must list at least one site",
    "List the sites, such as [site_a, site_b].",
)
SCENARIO_SITE_TWICE = Message("the site {site} is listed twice", "List each site once.")
SCENARIO_UNKNOWN_SITE = Message(
    "unknown site {site}", "Use a site listed under `sites`: {sites}."
)
SCENARIO_NO_OUTPUTS = Message(
    "must list at least one output",
    "List omop, patients, or both.",
)
SCENARIO_UNKNOWN_OUTPUT = Message(
    "unknown output {value}", "List omop, patients, or both."
)
SCENARIO_NAME_TAKEN = Message(
    "{value} is already the name of an earlier row",
    "Give each row of a table its own name.",
)
SCENARIO_ONE_ROW = Message("must have at most one row", "Remove the extra rows.")
SCENARIO_ID_TAKEN = Message(
    "{value} is already the id of an earlier individual",
    "Give each individual its own id, or leave `id` out.",
)
SCENARIO_BIRTH_DATETIME = Message(
    "birth_datetime is set from year_of_birth, month_of_birth and day_of_birth",
    "Remove birth_datetime, and give those columns instead.",
)
SCENARIO_NULL_SOURCE_VALUE = Message(
    "`source_value` must not be null",
    "Give each individual a source_value.",
)
SCENARIO_ZIP_UNQUOTED = Message(
    "ZIP codes must be written in quotes",
    'Write it in quotes, such as "02118": unquoted, 02118 is read as a number.',
)
SCENARIO_NOT_AT_SITE = Message(
    "this individual has no record at the site {site}",
    "Add the site to the individual's `sites`, or remove this entry.",
)
SCENARIO_NOT_PII_OR_PERSON = Message(
    "not a PII key ({pii_keys}) or a person column",
    "Use one of those keys, or a column of the person table.",
)
SCENARIO_FIXED_PERSON_COLUMN = Message(
    "identifies the individual or gives their date of birth, so it cannot "
    "differ between sites",
    "Give it on the individual itself, not under `at_sites` or `person_attributes`.",
)
SCENARIO_PERSON_TABLE = Message(
    "person columns are written directly on the individual",
    "Move these keys up to the individual.",
)
SCENARIO_OBSERVATION_PERIOD = Message(
    "observation periods are built from the individual's events",
    "Remove this list.",
)
SCENARIO_VISIT_TABLE = Message(
    "visits are built from events",
    "Give visit columns under an event's `visit` instead.",
)
SCENARIO_SHARED_TABLE_ON_INDIVIDUAL = Message(
    "{table} is a shared table",
    "Move it to the top level of the scenario.",
)
SCENARIO_NO_VISITS = Message(
    "{table} rows are not linked to visits",
    "Remove `visit` from this event.",
)
SCENARIO_SET_BY_SYNTH = Message(
    "this column is set by synth",
    "Remove it; synth links events to their individual and visit.",
)
SCENARIO_NO_START_DATE = Message(
    "missing `{key}`",
    "Give every event its date, such as `{key}: 2023-01-05`.",
)
SCENARIO_VISIT_CONFLICT = Message(
    "events on {date} give conflicting {column} ({first} and {second})",
    "Events on one date share one visit: give them the same visit columns.",
)
SCENARIO_SOURCE_VALUE_TAKEN = Message(
    "the source_value {value} is used by more than one individual",
    "Give each individual its own source_value.",
)
SCENARIO_UNKNOWN_MODULE = Message(
    "unknown module {value}",
    "Use one of: {modules}.",
)
SCENARIO_MODULE_CONCEPT_ID = Message(
    "{value} is a concept clinical modules use, with the id {expected}, not {found}",
    "Give it the id {expected}, or another name.",
)
SCENARIO_PERSON_IDS = Message(
    "must be one of: {modes}",
    "Write `person_ids: shared` or `person_ids: per_site`.",
)
SCENARIO_ID_RANGE = Message(
    "per_site person ids need individual ids from 1 to {limit}, not {value}",
    "Give individuals smaller ids, or use `person_ids: shared`.",
)
SCENARIO_YEAR_SPAN = Message(
    "must be [first year, last year]",
    "Write two years, the first no later than the last, such as [2023, 2025].",
)
SCENARIO_RELATIVES_TOTAL = Message(
    "gave relatives chances that add up to {total}, over 1.",
    "Lower the chances so that they add up to 1 at most.",
)
SCENARIO_SHARING_TOTAL = Message(
    "the chances must add up to 1, not {total}",
    "Change one_site, several_sites and all_sites so they add up to 1.",
)
SCENARIO_EMPTY_POOL = Message("must not be empty", "List at least one value.")
SCENARIO_POOL_TOO_SMALL = Message(
    "a change_rate of 1 needs at least two values to choose between",
    "Add a value to the pool, or lower change_rate.",
)
SCENARIO_BAD_WEIGHT = Message(
    "must be a whole number of 1 or more, not {value}",
    "Give a weight such as 10.",
)
SCENARIO_BAD_MAX_EDITS = Message(
    "must be 1 or more, not {value}", "Give the number of typos, such as 1."
)
SCENARIO_INVALID_SITE = Message(
    'the site name "{site}" may contain only letters, digits and underscores',
    "Rename the site, such as site_a.",
)

# ---------------------------------------------------------------------------
# Labels: the first column of a validation table
# ---------------------------------------------------------------------------


class Label:
    """Labels for check tables. A label with a {placeholder} is a Message."""

    CONFIG_FILE = "config file"
    SCENARIO = "scenario"
    SCENARIO_CONTENT = "scenario content"
    OUTPUT_FILE = "output file"
    REPORT_FILE = "report file"
    OUTPUT_FOLDER = "output folder"
    FLAGGED_FILE = "flagged file"
    STUDY_IDS_COLUMNS = "study_ids columns"
    SELECTED_FILE = "selected tables"
    TABLE_KEY = Message("{table} primary key")
    SELECTION = "selection rules"
    TABLE_COLUMNS = Message("{table} columns")
    PATIENTS_FILE = "patients file"
    PATIENTS_CONTENT = "patients content"
    SITES = "sites"
    PROJECT_NAME = "name"
    BUNDLE = Message("{who} bundle")
    GUIDE = Message("{who} guide")
    CONTACTS = "contacts"
    TIMES = "times"
    WHEEL = "wheel"
    SITE = "site"
    METADATA_COLUMNS = "metadata columns"
    IDS = "ids"
    IDENTIFIERS = "identifying values"
    COLUMNS = "columns"
    SECRET = "secret"  # noqa: S105 (a label, not a secret)
    SITE_KEY = Message("{site} key")
    PRIVATE_KEY = "private key"
    KEYS = "public keys"
    FIELDS = "fields"
    ID_COLUMN = "id"
    THRESHOLD = "threshold"
    HASH_LENGTH_SETTING = "hash_length"
    FILL_RATIO = "fill_ratio"
    XOR_FOLDS = "xor_folds"
    PROJECT_SALT = "project_salt"
    RECORD_SALT = "record_salt"
    SECRET_CHECK = "secret_check"  # noqa: S105 (a label, not a secret)
    SAME_SECRET = "same secret"  # noqa: S105 (a label, not a secret)
    THRESHOLDS = "thresholds"
    WORKERS = "workers"
    HASH_FILES = "hash files"
    HASH_FILE = Message("hash file {n}")
    HASH_RECORDS = Message("hash file {n} rows")
    HASH_VALUES = Message("hash file {n} hashes")
    SITE_PAIRING = "site pairing"
    HASH_LENGTH = "hash length"
    LINKAGES_FILE = "linkages file"
    LINKAGE_REPORT = "linkage report"
    TABLES = "tables"
    OUTPUT = "output"
    SETTINGS = "settings"
    INSPECT_FILE = "file"
    TABLE = Message("{table}")
    SITE_FILE = Message("{site} tables")
    TABLE_SITE = Message("{table}/{site}")
    TABLE_SITE_COLUMNS = Message("{table}/{site} columns")
    TABLE_COLUMN = Message("{table}.{column}")
    FORMAT = Message("{column} format")
    PARTITION = Message("{table}[{value}]")


# What each file is, for messages such as "Cannot find the {what} {name}".
class Kind:
    PATIENTS = "patient file"
    INPUT = "file"


# ---------------------------------------------------------------------------
begin_section("Progress")
# ---------------------------------------------------------------------------

HASH_BITS = Message("{name} has {bits} bits")

# Shown when a command's work is large enough to take a while.
STOPPABLE = Message("Ctrl+C to cancel (all progress will be lost)")

# The line a command's bar leaves in a terminal when its work is done.
PROGRESS_DONE = Message("pprl {command} ({time})")

# The words on each command's bar, one per step. Each is at most 24
# characters and never changes: no file name, site name, table name or count.
STEP_READING_TABLES = "Reading tables"
STEP_SELECTING = "Selecting rows"
STEP_WRITING_SELECTED = "Writing selected file"
STEP_READING_PATIENTS = "Reading patient file"
STEP_CLEANING = "Cleaning records"
STEP_WRITING_CLEANED = "Writing cleaned file"
STEP_READING_CLEANED = "Reading cleaned file"
STEP_HASHING = "Hashing records"
STEP_WRITING_HASHES = "Writing hash file"
STEP_READING_SELECTED = "Reading selected file"
STEP_REPLACING_IDS = "Replacing ids"
STEP_CHECKING_TABLES = "Checking tables"
STEP_SORTING = "Sorting rows"
STEP_WRITING_PACKED = "Writing packed file"
STEP_READING_HASHES = "Reading hash files"
STEP_COMPARING = "Comparing records"
STEP_GROUPING = "Grouping records"
STEP_BEST_SCORES = "Finding best scores"
STEP_WRITING_LINKAGE = "Writing linkage file"
STEP_READING_SITES = "Reading site files"
STEP_SCANNING = "Scanning tables"
STEP_MERGING = "Merging tables"
STEP_PREPARING = "Preparing records"
STEP_LINKING = "Linking records"
STEP_GENERATING = "Generating individuals"
STEP_WRITING_SITES = "Writing site files"
STEP_CREATING_WORKSPACE = "Creating workspace"
STEP_READING_WORKSPACE = "Reading workspace"
STEP_READING_FILE = "Reading file"
STEP_WRITING_BUNDLES = "Writing bundles"
STEP_LOCKING_SECRETS = "Locking secrets"
STEP_WRITING_GUIDES = "Writing guides"
STEP_READING_REPORT = "Reading report"
STEP_DELETING = "Deleting files"

# Labels for inspect.
INSPECT_LABELS = {
    "file": "File:",
    "type": "Type:",
    "site": "Site:",
    "role": "Role:",
    "project": "Project:",
    "set": "Bundle set:",
    "release": "Release:",
    "created": "Written:",
    "member": "Holds:",
    "bits": "Bits:",
    "input": "Made from:",
    "rows": "Rows:",
    "columns": "Columns:",
}
INSPECT_CSV = "a CSV file"
INSPECT_MEMBER = Message("{name}: {rows:row(s)}; columns {columns}")
INSPECT_SETTINGS = Message("{name}, below")
INSPECT_INPUT = Message("{kind}{site}")

# ---------------------------------------------------------------------------
begin_section("Help")
# ---------------------------------------------------------------------------

HELP_TITLE = Message("pprl {version} · Privacy-Preserving Record Linkage")
HELP_USAGE = "Usage: pprl <command>, in your workspace folder"
# Every command, grouped by what it works on.
HELP_GROUPS = (
    (
        "Workspace:",
        (
            (
                "init",
                "Create or inspect a workspace",
            ),
            ("inspect <file>", "Inspect a .pprl or .csv file in this workspace"),
            ("destroy", "Delete every file in this workspace permanently"),
        ),
    ),
    (
        "Project:",
        (
            ("synth", "Generate synthetic data from synth.yml"),
            ("sweep", "Compare linkage across thresholds or schemas, from sweep.yml"),
            ("bundles", "Write every role's bundle from project.yml"),
            ("secrets", "Lock a new secret to each site's public key"),
            ("guides", "Write every role's guide from project.yml and deployment.yml"),
            ("review", "Check a linkage, from the linkage agent's report"),
        ),
    ),
    (
        "Data:",
        (
            ("clean", "Filter and validate patient identifiers for hashing"),
            ("hash", "Hash a set of patient identifiers"),
            ("select", "Filter and validate data files for merging"),
            ("pack", "Consolidate data files for the merge agent"),
            ("link", "Link the sites' hash files"),
            ("merge", "Merge the sites' data based on linkages"),
        ),
    ),
)
HELP_FOOTER = "Run pprl <command> --help for what a command reads and writes."
# A command's own help (pprl <command> --help): its usage, and the files it
# reads and writes, by their names in the workspace. {site} is this
# workspace's site, or <site> outside a workspace.
HELP_LABELS = {
    "usage": "Usage:",
    "reads": "Reads:",
    "writes": "Writes:",
}
HELP_COMMAND_TITLE = Message("{command} · {description}")
HELP_ARGUMENTS = "Arguments:"
HELP_SOME_SITE = "<site>"
HELP_WHERE = ", in your workspace folder"
HELP_USAGES = {
    "init": (
        "pprl init, in the new folder you put your bundle in",
        "pprl init, in a new folder holding project.yml, for a new project",
        "pprl init, in a workspace",
    ),
    "destroy": ("pprl destroy, in your workspace folder",),
}
# A role command's files, as its help lists them. {name} is what it reads: a
# folder of tables, or each site's file by name. {names} is what it writes:
# files kept in the workspace, or sent from the outbox.
HELP_READS = {
    "folder": Message("each table in {name}"),
    "each site": Message("each site's {name}"),
}
HELP_WRITES = {
    "folder": Message("{name}, a CSV file per table"),
    "kept": Message("{names}, kept in this workspace"),
    "sent": Message("{names}, for {recipient}"),
}
# The files that the other commands read and write.
HELP_FILES = {
    "inspect": ("the file you name", "nothing"),
    "init": (
        "the bundle in this folder",
        "pprl-workspace.yml, the log and a site's keys, in this folder",
    ),
    "synth": ("synth.yml", "synth/, a folder of CSV files per site"),
    "sweep": (
        "sweep.yml, and the files it names",
        "a CSV file of the results",
    ),
    "bundles": ("project.yml", "every role's bundle, in outbox/<recipient>/"),
    "secrets": (
        "project.yml, and each site's public key",
        "each site's secret file, in outbox/<site>/",
    ),
    "guides": (
        "project.yml and deployment.yml",
        "every other role's guide, in outbox/<recipient>/, and coordinator.guide.html",
    ),
    "review": ("linkage_report.pprl", "linkage_report.svg, beside it"),
    "destroy": (
        "this workspace, to list its files",
        "a receipt in this folder, once every other file is deleted",
    ),
}
HELP_INSPECT_ARGUMENT = "A pprl file or CSV file in the workspace"
# Command-line arguments, as messages describe them.
ARGUMENT_NAMES = {
    "file": "the file to describe",
}
# How each command is written. Where it takes a name, the name is given in
# words, so that no example can be typed in as it is.
HELP_EXAMPLES = {
    "inspect": Message("pprl inspect, then the file's name"),
    "default": Message("pprl {command}"),
}

# Text for review.
REVIEW_NOT_FOUND = Message(
    "Cannot find {name} in this workspace.",
    "Save the linkage report from the linkage agent in this workspace, under the "
    "name it has, {name}.",
)
REVIEW_UNREADABLE = Message(
    "{name} holds no linkage that pprl review can read.",
    "Ask the linkage agent to send {name} again, as pprl link wrote it.",
)
REVIEW_SAME_SECRET = Message(
    "Same secret: {first}-{second}, bit correlation {correlation:.2f}."
)
REVIEW_SECRET_OFF = Message("Same secret: {first}-{second}, not checked (turned off).")
REVIEW_SECRET_UNCHECKED = Message(
    "Same secret: {first}-{second}, not checked (too few records)."
)
REVIEW_PAIR = Message(
    "Linked pairs: {first}-{second} {linked:,} ({first} {first_share:.1%}, "
    "{second} {second_share:.1%})."
)
REVIEW_BEST = Message("Best scores at each other site, {count:,} ({scope}):")
REVIEW_ALL = "every record"
REVIEW_SAMPLED = Message("a sample, up to {sample:,} per site")
REVIEW_BELOW = Message("below {low:.2f}")
REVIEW_NEAR = Message("{low:.2f}-{threshold:.2f} near miss")
REVIEW_WEAK = Message("{threshold:.2f}-{high:.2f} weak")
REVIEW_ABOVE = Message("{high:.2f} and up")
REVIEW_FALSE = Message(
    "False pairs, estimated: {first}-{second} {false:,} (95% range {low:,}-{high:,})."
)
REVIEW_FALSE_OPEN = Message(
    "False pairs, estimated: {first}-{second} {false:,} (95% range {low:,} or more)."
)
REVIEW_MOSTLY_FALSE = Message(
    "False pairs, estimated: {first}-{second}, half or more of the linked pairs."
)
REVIEW_NO_WARNINGS = "Warnings from link: none."
REVIEW_WARNING = Message("From link: {text}")
REVIEW_WROTE = Message("Wrote {name}.")
REVIEW_TITLE = "Best scores, every site"
REVIEW_SCORE = "Dice similarity"
REVIEW_RECORDS = Message("Records (≤{sample:,} per site, log scale)")
REVIEW_THRESHOLD = Message("Threshold {threshold:.2f}")
REVIEW_BAND = Message("Review band {low:.2f} to {high:.2f}")


def help_for(command: str, groups: Iterable = HELP_GROUPS) -> str:
    """Return the one-line description of a command."""
    for _, commands in groups:
        for name, description in commands:
            if name.split()[0] == command:
                return description
    raise KeyError(command)
