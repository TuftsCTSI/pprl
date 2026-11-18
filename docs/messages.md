# Every message pprl shows

<!-- Generated from src/pprl/_messages.py by tests/message_catalog.py. Do not edit by hand. -->

Every message a user can see is defined in src/pprl/_messages.py; this page lists them for review. A problem has a fix, shown after it as `To fix:`. Words in {braces} are filled in with the details of each case: names of files, columns and settings, counts and row numbers, but never a value from a patient file, an id of any kind, or the secret. A count is shown with its noun, singular or plural: `{count:row(s)}` reads 1 row, or 1,200 rows, and `{rows:row(s)}` reads row 7, or rows 7, 9. A problem with the project's settings, which only the coordinator can put right, asks pprl's users to send the log, and gives the fix the commands on the project show in brackets. Where a message holds text the user supplied, such as a folder's name or an export's column names, the terminal shows it, and the log has <withheld> in its place. Technical details are written to the log only, each after [INFO].

`make docs` (or `python -m tests.usability_cases`) writes build/input-handling.md, which shows the message each kind of input gets.

## Results and summaries

| Message | To fix |
| --- | --- |
| Nothing was written. |  |
| Wrote {count:hash(es)} from {site} to {output}. |  |
| Found {groups:group(s)} of linked records. Wrote {output}. |  |
| Wrote {output}, the linkage report for the coordinator. It holds counts only. |  |
| {first} and {second}: {count:record(s)} linked |  |
| {first} and {second}: {weak:pair(s)} scored {threshold:.2f} to {high:.2f}, just above the threshold. |  |
| {site}: about {near:record(s)} not linked to {other} scored {low:.2f} to {threshold:.2f}, just below the threshold (estimated from {sample:record(s)}). |  |
| {first} and {second}: roughly {false:false pair(s)} ({low:,} to {high}), from the records with two or more candidates. |  |
| {first} and {second} linked {share:.0%} of the records of {smaller}, the smaller site. | This can be right, if nearly every patient of one site is the other's too. Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {first} and {second}: roughly {share:.0%} of the linked pairs may be false, from the records with two or more candidates. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {first} and {second}: about half or more of the linked pairs may be false, from the records with two or more candidates. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {first} and {second}: {share:.0%} of the linked pairs scored just above the threshold. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {first} and {second} hashed with different secrets: how often each bit is set does not agree (correlation {correlation:.2f}). | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {first} and {second}: no records linked | The linkage file was written, but if these sites share patients, the file lacks those pairs. Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {table}: kept {kept:,} of {rows:row(s)}. |  |
| Left out {count:row(s)}: {rule}. |  |
| Emptied {columns} in every row kept. |  |
| {table}: {column} had {values} that its rule does not list, and no row of {absent}, which it does: a value may have been renamed. | pprl select wrote the rows it kept, without these. Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| Wrote {count:table(s)} to {output}, for {people:patient(s)}. |  |
| Left out {count:row(s)} of {name}, whose {id} is in none of the selected tables. |  |
| Wrote {kept:row(s)} to {output}, each with a new study_id in place of its {id}, and {flagged:set-aside row(s)} to {flagged_name}. |  |
| Kept {count:record(s)} not in the linkage file, each as a person of their own. |  |
| Where the sites' values tie, the first site's is kept, in this order: {sites}. |  |
| {table}: {rows_in:row(s)} from the sites, merged into {rows_out:row(s)}. |  |
| {column}: {conflicts:merged row(s)} where the sites differed{how}; {changed:site value(s)} not kept; {empty:merged row(s)} with no value. |  |
| {count:,} by {criterion} |  |
| {strategy} on {column} |  |
| each from the row its {lead} came from |  |
| pprl's own check of the merged {table} failed: {check}. Nothing was written. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| Wrote {count:merged table(s)} to the folder {output}. |  |
| Wrote the dataset to the folder {output}: {individuals:individual(s)} at {sites:site(s)}. |  |
| Linked at {count:threshold(s)}. |  |
| Linked at {count:threshold(s)}. Wrote {output}. |  |

## Command line

| Message | To fix |
| --- | --- |
| {program} has no command {command}. | Use one of: {commands}. |
| {command} needs {missing} on the command line. | Write it as {example}. |
| {command} was given more than it reads: {extra}. | Write it as {example}. |
| pprl could not read the command line ({detail}). | Run pprl alone to see the commands. |
| pprl stopped: the drive is full. Nothing it was writing was kept. | Free some space, then run the command again. |
| pprl stopped because of an unexpected problem: {kind} in {place} (pprl {version}). | Nothing it was writing was kept. Send your log (the .log file in this folder) to the coordinator over a secure channel. |

## Workspace

| Message | To fix |
| --- | --- |
| This folder is not a pprl workspace. | Open a terminal in your workspace folder, or run pprl init in a new folder holding your bundle. |
| pprl-workspace.yml in this folder is damaged. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| This workspace uses pprl {release}, but this is pprl {version}. | Install the release your guide names. |

## Creating a workspace (pprl init)

| Message | To fix |
| --- | --- |
| This folder holds no bundle and no project.yml. | Put your bundle here, then run pprl init again. |
| This folder holds {count:bundle(s)}, and pprl cannot tell which is yours: {names}. | Remove every bundle but yours, then run pprl init again. |
| {name} cannot be used: {reason}. | Download it again, or install the release your guide names. |
| Created the workspace for {owner} in {project}: |  |
| Created the workspace for {owner}, for a new project: |  |
| This folder is the workspace for {owner} in {project}: |  |
| This folder is the workspace for {owner}: |  |
| {status}: {what} |  |

## Destroying a workspace (pprl destroy)

| Message | To fix |
| --- | --- |
| This folder, {folder}, the workspace for {owner}, holds {count:file(s)}, {size} in all: |  |
| This workspace was created with pprl {release}; this is pprl {version}. |  |
| Deleted {count:file(s)} permanently. |  |
| Nothing was deleted: pprl could not write its receipt in this folder. | Check that you can create files in this folder, then run pprl destroy again. |
| {count:file(s)} in this folder could not be deleted, and are left: | Close any program that has one of them open, then run pprl destroy again. |
| {owner}.receipt_{date}{suffix}.txt |  |
| Wrote the receipt {name}. |  |
| Nothing was deleted. | To delete them, run pprl destroy again, and type "yes". |
| This folder is not a pprl workspace, so nothing was deleted. | Open a terminal in your workspace folder, and run pprl destroy there. |

## Bundles

| Message | To fix |
| --- | --- |
| `name` should be the project's name, such as Study 2026, but is {value}. | Write the project's name after `name:` in project.yml. |
| `sites` should list 2 or more site names, but is {value}. | Write them as a list, such as `sites: [site_a, site_b]`; the first is the one whose values merge prefers. |
| `sites` lists {sites} more than once. | List each site once. |
| `sites` lists {sites}, which would name a site's bundle as another file. | Name each site anything but {names}. |
| {name} already exists, and pprl never replaces a file or folder. | To write new bundles, first delete every earlier one. |
| Wrote {names}. |  |
| {name} already exists, and pprl never replaces a file or folder. | To make a new secret, first delete every site's earlier secret file, in outbox/<site>/. |
| The sites' public keys are of different sets of bundles. | Ask each site participant with an earlier bundle to make a new workspace from the latest one, and to send you the new public key. |
| Locked one new secret to each site's public key. Wrote {names}. |  |
| The bundle {name} is missing from this workspace. | Put the copy you received back in this folder, unchanged, or ask the coordinator to send it again. |
| The bundle {name} cannot be used: {reason}. | Put the copy you received back in this folder, unchanged, or ask the coordinator to send it again. |
| {command} needs the setting `{setting}`, which the bundle {name} does not have. | Run {command} in a workspace whose bundle has it. |
| {command} needs the setting `{setting}`, and this workspace has no bundle. | Run {command} in a workspace whose bundle has it. |

## Guides

| Message | To fix |
| --- | --- |
| `contacts` should give the name and email address of one person for each of {keys}, but {problem}. | Write one line for each, such as `site_a: {{name: Ana Example, email: ana@example.org}}`. |
| `times` should give each of {keys}, written yyyy-mm-dd HH:MM, but {problem}. | Write each time as yyyy-mm-dd HH:MM, on a 24-hour clock, such as `destroy: 2026-11-16 17:00`. |
| `times` has {later} ({later_time}) before {earlier} ({earlier_time}). | Give the times in the order the project runs: {keys}. |
| `wheel` should be the https address of pprl's wheel, optionally ending in #sha256= and the wheel's 64-character SHA-256, but is {value}. | Leave `wheel` out to use the wheel of pprl {release} on GitHub, or give the address your sites install from. |
| {name} already exists, and pprl never replaces a file or folder. | To write them again, first delete every earlier guide. |
| Wrote {guides}, one for each recipient, and {own}, for you. |  |

## Run log

| Message | To fix |
| --- | --- |
| ===== {time} · pprl {version} · {role} · {command} ===== |  |
| {system} · Python {python} |  |
| ----- exit code {code} ----- |  |
| pprl could not open its log file, {log} ({reason}). Nothing was done. | Check that you can create and change files in this folder, then run the command again. |
| pprl stopped because it could not write to its log file, {log} ({reason}). | Nothing it was writing was kept. Free some space, or reconnect the drive, then run it again. |
| pprl was stopped (Ctrl+C). Nothing it was writing was kept. | Run it again when you are ready. |
| pprl can't run on this computer ({system} on {machine}). It runs on Linux and Windows with x86-64 processors, and on macOS. | Run pprl on a supported computer. If you can't, contact the coordinator. |
| A library pprl uses gave a warning ({kind}, in {place}). The run went on. | Nothing is needed: it is in your log, should the coordinator ask for it. |

## Run log: [INFO] lines (logged, never shown)

| Message | To fix |
| --- | --- |
| installed: {how} |  |
| packages: {packages} |  |
| computer: {cpus:CPU(s)} · {memory} memory |  |
| read {name}: {size} · {rows:row(s)} · {columns:column(s)} · {encoding} · {endings} line endings |  |
| read {name}: {size} · {kind} · pprl {release} · file {id} · bundle set {set} · {members}{inputs} |  |
| {name} {rows:row(s)} × {columns:column(s)} |  |
|  · made from {files} |  |
| {kind} {id} |  |
| step: {step} · {seconds:.2f} s |  |
| crash: {chain} |  |
| {file}, line {line}, in {function} |  |
|  · raised while handling {kind} |  |
| other bundles here: {names} |  |
| finished: {seconds:.2f} s · peak memory {memory} |  |
| link same secret, {first} and {second}: bit correlation {correlation} |  |
| link pair {first} and {second}: {linked:,} linked, of {first_records:,} and {second_records:,} records |  |
| link best scores of every site's sample, by 0.01: {counts} |  |
| link warning: {text} |  |
| link threshold {threshold}, review band {low} to {high} |  |
| link false pairs of {first} and {second}: {estimate}, from {low} to {high}; {several} records with several candidates, of {candidates} with any |  |
| merged {table}{part}: {rows_in:row(s)} in, {rows_out:row(s)} out |  |
| merged {table}{part}, {column}: {conflicts:,} differed{how}, {changed:,} not kept, {empty:,} with no value |  |
| , partition {value} |  |

## Config files

| Message | To fix |
| --- | --- |
| Cannot find the config file {name} in this workspace. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Put it in this workspace, with exactly that name.) |
| The config file {name} is empty. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each setting on a line of its own, as its name, a colon and its value, such as `threshold: 0.80`.) |
| The config file {name} cannot be read: its layout is wrong near line {line}, column {column}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Correct that line; each setting is one line, such as `threshold: 0.80`.) |
| The config file {name} does not hold settings. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each setting on a line of its own, as its name, a colon and its value, such as `threshold: 0.80`.) |
| The config file {name} has the {keys:setting(s)}, which this command does not read. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Remove them, or correct their spelling. The settings it reads are: {allowed}.) |
| The config file {name} sets {keys}, which only pprl itself may set. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Remove those lines from {name}.) |
| The config file has no `{key}` setting. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Add `{key}:`.) |
| `{key}` should be one file name, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write a single file name, such as `{key}: site_a.csv`.) |
| `{key}` should be true or false, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write `{key}: true` or `{key}: false`.) |

## Files and folders

| Message | To fix |
| --- | --- |
| {what} is outside the workspace ({name}), and pprl reads and writes files only inside it. | Move the file into the workspace, and give its name there, such as site_a/patients.csv. |
| Cannot find the {what} {name} in this workspace. | Put it in this workspace, with exactly that name. |
| {name} already exists, and pprl never replaces a file or folder. | Move or delete the earlier {name}. |
| {name} is empty (0 bytes). | Check that it is the right file, saved completely. |
| {name} has column names but no rows. | Check that it is the right file, exported completely. |
| {name} is not a well-formed CSV file{near}. | Export it again, and save it as CSV UTF-8. |
| {name} is not saved as UTF-8 text. | Save it again as CSV UTF-8 (in Excel: File > Save As > CSV UTF-8). |
| {name} has empty rows before its last row: {rows:row(s)} (the header is row 1). | Delete the empty rows, or check that the file was exported completely. |
| {name} has {count:row(s)} without {columns} values, as its header has: {rows:row(s)} (the header is row 1). | Export it again: those rows have a missing or extra comma, or were cut off. |
| {name} separates its values with semicolons or tabs, not commas. | Save it again as CSV, with commas (in Excel: File > Save As > CSV UTF-8). |
| {name} has {count:column(s)} without a name, often empty columns left at the right of a spreadsheet. | Delete those columns, then save the file again. |
| {name} has spaces around the {columns:column name(s)}. | Remove the spaces from the first row of the file. |
| {name} has more than one column named {columns}. | Keep one column of each name. |

## pprl files

| Message | To fix |
| --- | --- |
| {name} is not a pprl file. | If you received it, download it again, and use it without opening it. If pprl wrote it here, delete it, and run again the command that writes it. |
| {name} is damaged: it is not whole, or not as pprl wrote it. | If you received it, download it again, and use it without opening it. If pprl wrote it here, delete it, and run again the command that writes it. |
| {name} is a bundle, not the file your guide names. | Delete it from this workspace, and wherever you shared it, now. Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {name} is a {found}, not a {expected}. | Use the file your guide names, saved under that name. |
| {name} was written by pprl {release}, but this is pprl {version}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {name} is from another project. | Use the file sent for this project. |
| {name} was made with other bundles than this workspace's: an earlier set, or another project's. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {name} holds the records of {found}, not {site}. | Use {site}'s own file, saved under the name your guide gives. |
| {name} and the linkage file come from different runs of pprl clean at {site}: the study_ids would join the wrong patients' records. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |

## File contents

| Message | To fix |
| --- | --- |
| {name} is missing the {columns:column(s)}. Its columns are: {found}. | Make the first row of the file match the column names in your guide, capitals included. |
| {name} has the {columns:column(s)}, which pprl does not use. | Remove them from the file. |
| {name} uses {count:value(s)} of {column} on more than one row: {rows:row(s)}. | Use the file pprl wrote, unchanged. |
| The site name "{site}" cannot be used: a site name may contain only letters, digits and underscores. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Use the site name your guide gives.) |
| The {column} column of {name} has {count:value(s)} not {format}: {rows:row(s)}. | Run pprl clean on the original patient file: it repairs what it can and sets the rest aside. {how} |
| {name} has {count:row(s)} whose {column} is empty, or has spaces around it: {rows:row(s)} (the header is row 1). | Use the file pprl wrote, unchanged. |
| {name} has {count:row(s)} with no identifying value at all (every field is empty): {rows:row(s)}. | Run pprl clean on the original patient file: it repairs what it can and sets the rest aside. |

## Hashing: secret, fields and hashing settings

| Message | To fix |
| --- | --- |
| {name} was not locked to this workspace's public key. | Send outbox/coordinator/{site}.public_key.pprl to the coordinator over a secure channel, and ask for a new secret file. |
| The config file has no `fields`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each field to hash on a line of its own under `fields`, with its weight, such as `name: 0.45`, or with its weight and type, such as `dob: {{weight: 0.25, type: date}}`.) |
| The config file has no `id`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Set `id` to the name of the column of each site's patient file and tables that holds the site's own id for each patient, such as `id: person_id`.) |
| `id` should be the name of a column, without spaces around it, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Set `id` to the name of the column of each site's patient file and tables that holds the site's own id for each patient, such as `id: person_id`.) |
| `id` is {value}, a name pprl gives a column of its own. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Name the column the site's export uses, such as person_id. Set `id` to the name of the column of each site's patient file and tables that holds the site's own id for each patient, such as `id: person_id`.) |
| `id` is {value}, which is also one of `fields`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: The id is never hashed: Set `id` to the name of the column of each site's patient file and tables that holds the site's own id for each patient, such as `id: person_id`.) |
| `fields` should be a block of fields to hash, each with its weight, such as `name: 0.5` or `dob: {{weight: 0.25, type: date}}`, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each field to hash on a line of its own under `fields`, with its weight, such as `name: 0.45`, or with its weight and type, such as `dob: {{weight: 0.25, type: date}}`.) |
| The field {field} in `fields` has no `weight`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give it a weight above 0, such as `{field}: 0.45`, or `{field}: {{weight: 0.25, type: date}}` for a field that is not text.) |
| The field {field} in `fields` has the {keys:setting(s)}, which pprl does not read. A field may have weight and type. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each field to hash on a line of its own under `fields`, with its weight, such as `name: 0.45`, or with its weight and type, such as `dob: {{weight: 0.25, type: date}}`.) |
| The field {field} in `fields` has the type {value}, but the types are {types}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each field to hash on a line of its own under `fields`, with its weight, such as `name: 0.45`, or with its weight and type, such as `dob: {{weight: 0.25, type: date}}`.) |
| The weight of the field {field} should be a number above 0, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each field to hash on a line of its own under `fields`, with its weight, such as `name: 0.45`, or with its weight and type, such as `dob: {{weight: 0.25, type: date}}`.) |

## Linkage

| Message | To fix |
| --- | --- |
| `hashes` should list 2 or more hash files, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: List every site's hash file, such as `hashes: [site_a.hashes.pprl, site_b.hashes.pprl]`.) |
| `threshold` should be a number from 0.0 to 1.0, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give a number such as `threshold: 0.80`.) |
| `hash_length` should be a whole number of bits, a multiple of 8, from {lo} to {hi} and at least 8 for each field, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give the number of bits the project chose with pprl sweep, such as `hash_length: 1024`.) |
| `fill_ratio` should be a number above 0, up to {hi}, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give the fill ratio the project chose with pprl sweep, such as `fill_ratio: 1.0`.) |
| `xor_folds` should be a whole number of 0 or more, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Remove the line to leave hashes unfolded, or give a number such as `xor_folds: 1`.) |
| `project_salt` should be text, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Remove the line to hash without one, or write it as text, in quotes if it holds only digits.) |
| `secret_check` should be true or false, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write `secret_check: true`, or `secret_check: false` if the project uses `record_salt`.) |
| `record_salt` should be a block with a `field`, and perhaps a `length`, a whole number of 1 or more, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Remove the lines to hash without one, or write it as `record_salt: {{field: zip, length: 3}}`.) |
| The `field` of `record_salt` is {value}, which is not one of `fields`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Name one of the fields to hash.) |
| `workers` should be a whole number of 1 or more, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Remove the line to use every processor, or give a number such as `workers: 4`.) |
| More than one hash file comes from {sites}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {name} has hashes (the hash column) that cannot be read. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| The hash files have different lengths: {details}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |

## Threshold and weight sweeps

| Message | To fix |
| --- | --- |
| `thresholds` should be a list of numbers from 0.0 to 1.0, but is {value}. | Write it as a list, such as `thresholds: [0.9, 0.95, 0.975]`. |
| The config file has no `{key}` setting, which a sweep over {mode} needs. | Add `{key}`: a sweep over patient files needs `fields`, written as in project.yml, and `assessment: true` or `assessment: false`. |
| `patients` should list 2 or more patient files, but is {value}. | List the patient files, such as `patients: [site_a/patients.csv, site_b/patients.csv]`. |
| `assessment` needs patient files: hash files have study_ids, which pprl clean draws anew at each site, so nothing tells which records are the same person. | Remove `assessment`, or sweep the patient files synth wrote instead. |
| Configuration {index}/{total} ({label}): done |  |
| Configuration {index}/{total} ({label}): best F1 {f1:.2%} |  |
| Best: F1 {f1:.2%} (precision {precision:.2%}, recall {recall:.2%}) at {settings} |  |
| Suggested: {settings} ({matches:match(es)}) |  |

## Cleaning

| Message | To fix |
| --- | --- |
| {name} was not saved as UTF-8 text, so it was read as Windows text (Windows-1252). |  |
| In the {column} column, {what} in {count:row(s)} of {name}: {rows:row(s)} (the header is row 1). |  |
| Found no row in {name} for {count:patient(s)} in the selected tables, so pprl pack will leave out their rows. | Check that {name} and the tables were exported together. |
| Set aside {count:row(s)} of {name}, because {reason}: {rows:row(s)} (the header is row 1). |  |
| {flagged} lists each row set aside, and why. | Correct them in your own records and export {name} again, or leave them out. |

## Selecting a site's rows

| Message | To fix |
| --- | --- |
| `tables` in the bundle should give each table's columns, primary key and rules. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Ask the coordinator for a new bundle.) |
| `tables` in the bundle has the {tables:table(s)} without the column {column} in its primary key. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Ask the coordinator for a new bundle.) |
| `keep` of the table {table} should be a list of rules, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each rule on a line of its own under `keep`, as one of `- not_empty: score`, `- one_of: {{year: ["2023", "2024"]}}` or `- found_in: {{table: visits, on: [person_id]}}`.) |
| Rule {n} of the table {table}'s `keep` {problem}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each rule on a line of its own under `keep`, as one of `- not_empty: score`, `- one_of: {{year: ["2023", "2024"]}}` or `- found_in: {{table: visits, on: [person_id]}}`.) |
| The `found_in` rules of {tables} look in each other's tables, in a loop. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Let each table look only in tables that do not look back in it.) |
| `censor` of the table {table} should list some of its columns, none in its `primary_key`, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: List the columns to empty, such as `censor: [notes, comments]`.) |
| Cannot find {name} in this workspace. | Save each table your guide names in the folder {folder}, with that name. |
| {count:row(s)} of {name} that the project's rules keep share their {columns} with another: {rows:row(s)} (the header is row 1). | Correct the export, then run pprl select again. |

## Packing a site's tables

| Message | To fix |
| --- | --- |
| Left out {count:row(s)} of the table {table}, whose patients have no study_id: pprl clean set them aside, or they are not in {patients}. | {flagged} lists the patients set aside and why. Correct them in {patients} and run pprl clean again, or leave them out. |
| {cleaned} was not made from {selected}: pprl select ran again after pprl clean. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| {count:row(s)} of the table {table} in {name} do not meet the project's rules for it, so pprl select did not write them. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| Wrote {count:table(s)} to {output}, with study_ids in place of {id}. |  |

## Merging

| Message | To fix |
| --- | --- |
| `sites` should list the sites to merge, such as [site_a, site_b], but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: List each site's folder name, in the order that breaks ties.) |
| `tables` should describe each table to merge, but is {value}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give each table its `primary_key`, and under `column` a rule for each other column: `rank_by`, a `tether` or `drop: true`, such as `score: {{rank_by: [{{take_highest: {{column: score}}}}]}}`.) |
| Table names may contain only letters, digits and underscores: {names}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Use each table's file name without .csv.) |
| {name} has no table {table}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. |
| The table {table} should be a block of settings. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give each table its `primary_key`, and under `column` a rule for each other column: `rank_by`, a `tether` or `drop: true`, such as `score: {{rank_by: [{{take_highest: {{column: score}}}}]}}`.) |
| The table {table} has the {keys:setting(s)}, which pprl does not read. A table may have: {allowed}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Remove them, or correct their spelling.) |
| The table {table} needs a `primary_key` list that includes {column}, the project's `id`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Add it, such as `primary_key: [{column}]`.) |
| `primary_key` of the table {table} names the {columns:column(s)}, which the data does not have. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Correct the column names, or check the site files.) |
| The table {table} has both `column` and `partition_by`/`partitions`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Use `column` for one set of rules, or `partition_by` with `partitions` for rules by partition, but not both.) |
| `partition_by` of the table {table} is {column}, which is not in `primary_key`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Add {column} to `primary_key`, or partition by a column in it.) |
| The table {table} has `partition_by`, but no `partitions`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Add a `partitions` block with the rules for each partition. Give each table its `primary_key`, and under `column` a rule for each other column: `rank_by`, a `tether` or `drop: true`, such as `score: {{rank_by: [{{take_highest: {{column: score}}}}]}}`.) |
| The table {table} has {count:row(s)} with an empty {column}, in no partition. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Fill in {column} in the site files.) |
| The table {table} has {count:value(s)} of {column} with no entry under `partitions`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Add an entry under `partitions` for every {column} value in the data.) |
| `column` of the table {table} should give each column its rule. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give each table its `primary_key`, and under `column` a rule for each other column: `rank_by`, a `tether` or `drop: true`, such as `score: {{rank_by: [{{take_highest: {{column: score}}}}]}}`.) |
| The table {table} has the {columns:column(s)} without a rule under `column`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give each a rule: `rank_by`, a `tether`, or `drop: true`.) |
| The table {table} has rules for the {columns:column(s)}, which the data does not have. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Remove those rules, or check the site files.) |
| The rule for {column} should be a block of settings, such as `{{drop: true}}`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give each table its `primary_key`, and under `column` a rule for each other column: `rank_by`, a `tether` or `drop: true`, such as `score: {{rank_by: [{{take_highest: {{column: score}}}}]}}`.) |
| The rule for {column} has the {keys:setting(s)}, which pprl does not read. A rule may have: {allowed}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Remove them, or correct their spelling.) |
| The rule for {column} says neither how to choose between sites nor to drop the column. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Give it `rank_by`, a `tether`, or `drop: true`.) |
| `rank_by` for {column} should be a list of strategies, each with its settings. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write each strategy on its own line under `rank_by`, such as `- take_highest: {{column: score}}`.) |
| Item {position} of `rank_by` for {column} should name one strategy, with its settings. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write it like `take_highest: {{column: score}}`.) |
| The rule for {column} uses the strategy {strategy}, which pprl does not have. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Use one of: {strategies}.) |
| {strategy} in the rule for {column} needs exactly these settings: {settings}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Add any that are missing, and remove any others.) |
| {strategy} in the rule for {column} takes no settings. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write it as `{strategy}: {{}}`.) |
| {strategy} in the rule for {column} compares {compared}, which the data does not have. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Name a column of the table.) |
| {column} is tethered to {target}, which has no rule. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Tether it to a column that has `rank_by`.) |
| {column} is tethered to {target}, which has no `rank_by`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Tether it to a column that has `rank_by`.) |
| {column} has both a `tether` and `rank_by`. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Keep one: a tethered column takes its value from the same row as the column it is tethered to.) |
| The tethers of {column} lead back to {column}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Tether columns to a column with `rank_by`.) |
| take_highest needs numbers, but {column} has {count:value(s)} other than numbers, in {where}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Correct those values, or use another strategy.) |
| {strategy} needs dates, but {column} has {count:value(s)} other than dates written yyyy-mm-dd, in {where}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write dates as yyyy-mm-dd (optionally with a time, HH:MM:SS) in the site files.) |
| take_top_ordinal in the rule for {column} needs `scale`: a list of its values, lowest first. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Write it like `scale: [low, medium, high]`.) |
| {column} has {count:value(s)} not on the `scale` of take_top_ordinal, in {where}. | Send your log (the .log file in this folder) to the coordinator over a secure channel. (In a command on the project: Add every value the data uses to `scale`, or correct the data.) |
| {name}, table {table} |  |

## Synthetic data scenarios

| Message | To fix |
| --- | --- |
| Cannot find the scenario {name} in this workspace. | Place the scenario file or folder in this workspace, and check its name. |
| not valid YAML near line {line}, column {column} | Check the layout of that line. |
| expected keys such as `seed:` and `sites:`, not a list or a single value | Write the scenario as keys and values, one to a line, such as `seed: 42`. |
| already given in an earlier file | Give a key other than a list in one file only. |
| the name must start with a number that sets its load order | Rename it, such as 01_preamble.yml. |
| not an OMOP CDM v5.4 table or column, or a synth key | Correct the spelling. |
| expected keys and values | Write it as keys and values, one to a line, such as `name: value`. |
| expected a list | Write each item on a line of its own, starting with `- `. |
| unknown key | Use one of: {allowed}. |
| missing `{key}` | Add `{key}`. |
| must be a chance from 0 to 1, not {value} | Give a number from 0 to 1. |
| cannot read {value} as {kind} | Write {kind}. |
| unknown {table} {value} | Name a {table} the scenario lists, or give its id number. |
| write `{short}` (column names leave off the table name) | Rename the key to `{short}`. |
| write `{short}` for the {table} table | Rename the key to `{short}`. |
| only pprl itself may set these keys | Remove them from the scenario. |
| {key} rows belong to an individual | List them under an individual in `individuals`. |
| must be an integer or text, not {value} | Give a seed such as 2026. |
| must list at least one site | List the sites, such as [site_a, site_b]. |
| the site {site} is listed twice | List each site once. |
| unknown site {site} | Use a site listed under `sites`: {sites}. |
| must list at least one output | List omop, patients, or both. |
| unknown output {value} | List omop, patients, or both. |
| {value} is already the name of an earlier row | Give each row of a table its own name. |
| must have at most one row | Remove the extra rows. |
| {value} is already the id of an earlier individual | Give each individual its own id, or leave `id` out. |
| birth_datetime is set from year_of_birth, month_of_birth and day_of_birth | Remove birth_datetime, and give those columns instead. |
| `source_value` must not be null | Give each individual a source_value. |
| ZIP codes must be written in quotes | Write it in quotes, such as "02118": unquoted, 02118 is read as a number. |
| this individual has no record at the site {site} | Add the site to the individual's `sites`, or remove this entry. |
| not a PII key ({pii_keys}) or a person column | Use one of those keys, or a column of the person table. |
| identifies the individual or gives their date of birth, so it cannot differ between sites | Give it on the individual itself, not under `at_sites` or `person_attributes`. |
| person columns are written directly on the individual | Move these keys up to the individual. |
| observation periods are built from the individual's events | Remove this list. |
| visits are built from events | Give visit columns under an event's `visit` instead. |
| {table} is a shared table | Move it to the top level of the scenario. |
| {table} rows are not linked to visits | Remove `visit` from this event. |
| this column is set by synth | Remove it; synth links events to their individual and visit. |
| missing `{key}` | Give every event its date, such as `{key}: 2023-01-05`. |
| events on {date} give conflicting {column} ({first} and {second}) | Events on one date share one visit: give them the same visit columns. |
| the source_value {value} is used by more than one individual | Give each individual its own source_value. |
| unknown module {value} | Use one of: {modules}. |
| {value} is a concept clinical modules use, with the id {expected}, not {found} | Give it the id {expected}, or another name. |
| must be one of: {modes} | Write `person_ids: shared` or `person_ids: per_site`. |
| per_site person ids need individual ids from 1 to {limit}, not {value} | Give individuals smaller ids, or use `person_ids: shared`. |
| must be [first year, last year] | Write two years, the first no later than the last, such as [2023, 2025]. |
| gave relatives chances that add up to {total}, over 1. | Lower the chances so that they add up to 1 at most. |
| the chances must add up to 1, not {total} | Change one_site, several_sites and all_sites so they add up to 1. |
| must not be empty | List at least one value. |
| a change_rate of 1 needs at least two values to choose between | Add a value to the pool, or lower change_rate. |
| must be a whole number of 1 or more, not {value} | Give a weight such as 10. |
| must be 1 or more, not {value} | Give the number of typos, such as 1. |
| the site name "{site}" may contain only letters, digits and underscores | Rename the site, such as site_a. |

## Progress

| Message | To fix |
| --- | --- |
| {name} has {bits} bits |  |
| Ctrl+C to cancel (all progress will be lost) |  |
| pprl {command} ({time}) |  |
| {name}: {rows:row(s)}; columns {columns} |  |
| {name}, below |  |
| {kind}{site} |  |

## Help

| Message | To fix |
| --- | --- |
| pprl {version} · Privacy-Preserving Record Linkage |  |
| {command} · {description} |  |
| Cannot find {name} in this workspace. | Save the linkage report from the linkage agent in this workspace, under the name it has, {name}. |
| {name} holds no linkage that pprl review can read. | Ask the linkage agent to send {name} again, as pprl link wrote it. |
| Same secret: {first}-{second}, bit correlation {correlation:.2f}. |  |
| Same secret: {first}-{second}, not checked (turned off). |  |
| Same secret: {first}-{second}, not checked (too few records). |  |
| Linked pairs: {first}-{second} {linked:,} ({first} {first_share:.1%}, {second} {second_share:.1%}). |  |
| Best scores at each other site, {count:,} ({scope}): |  |
| a sample, up to {sample:,} per site |  |
| below {low:.2f} |  |
| {low:.2f}-{threshold:.2f} near miss |  |
| {threshold:.2f}-{high:.2f} weak |  |
| {high:.2f} and up |  |
| False pairs, estimated: {first}-{second} {false:,} (95% range {low:,}-{high:,}). |  |
| False pairs, estimated: {first}-{second} {false:,} (95% range {low:,} or more). |  |
| False pairs, estimated: {first}-{second}, half or more of the linked pairs. |  |
| From link: {text} |  |
| Wrote {name}. |  |
| Records (≤{sample:,} per site, log scale) |  |
| Threshold {threshold:.2f} |  |
| Review band {low:.2f} to {high:.2f} |  |

## Words used inside messages

| Where | Words |
| --- | --- |
| a value not in its format, a date field | dates written yyyy-mm-dd. Dates are written yyyy-mm-dd, such as 1990-03-15; a spreadsheet may have changed their format when the file was saved. |
| a value not in its format, a zip field | 5-digit ZIP codes. ZIP codes are 5 digits; a spreadsheet may have dropped a leading zero (2101 for 02101). |
| a value not in its format, a state field | 2-letter state codes in capitals. States are 2 capital letters, such as MA. |
| what clean repaired | removed spaces around the value |
| what clean repaired | removed a time after the date |
| what clean repaired | shortened a ZIP+4 code to its first 5 digits |
| what clean repaired | wrote the state code in capitals |
| why clean set a row aside | its {column} is empty |
| why clean set a row aside | its {column} is also used on another row |
| why clean set a row aside | every identifying value is empty |
| why clean set a row aside | its {column} is not a date written yyyy-mm-dd |
| why clean set a row aside | its {column} has 4 digits, so it may have lost a leading zero |
| why clean set a row aside | its {column} is not a 5-digit ZIP code |
| why clean set a row aside | its {column} is not a 2-letter state code |
| a kind of pprl file | bundle |
| a kind of pprl file | private key file |
| a kind of pprl file | public key file |
| a kind of pprl file | secret file |
| a kind of pprl file | selected tables file |
| a kind of pprl file | cleaned patient file |
| a kind of pprl file | hash file |
| a kind of pprl file | packed tables file |
| a kind of pprl file | linkage file |
| a kind of pprl file | linkage report |
| inspect, a line it shows | File: |
| inspect, a line it shows | Type: |
| inspect, a line it shows | Site: |
| inspect, a line it shows | Role: |
| inspect, a line it shows | Project: |
| inspect, a line it shows | Bundle set: |
| inspect, a line it shows | Release: |
| inspect, a line it shows | Written: |
| inspect, a line it shows | Holds: |
| inspect, a line it shows | Bits: |
| inspect, a line it shows | Made from: |
| inspect, a line it shows | Rows: |
| inspect, a line it shows | Columns: |
