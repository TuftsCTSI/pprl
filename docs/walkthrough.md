# Walkthrough

This is hands-on training for developers and coordinators. You'll run a complete project on synthetic (made-up) data, playing every role yourself, from the coordinator's first command to the last workspace being destroyed.

This example project links two sites' patients then merges each site's OMOP `observation_period` table into one dataset.

Any pprl commands should be run in the role's subdirectory (which you'll create during the walkthrough).

In a real project, each person plays one role, follows their own guide, and sends files to other roles over a secure channel. Here, you play every role, so the steps come in two kinds:

- **A role's step**, such as "site_a participant: creates the workspace", is what that role's guide says to do. Under its heading, a **Guide** line names the guide and its steps, so you can read along in the guide as you go.
- **A transfer**, such as "Transfer: site_a → coordinator", stands in for a transfer over a secure channel. You simply copy the files between folders yourself.

Each copy command names only the folder it copies into, ending in `/`, so the file keeps its name. The folder must already exist. The commands work in a macOS or Linux terminal, and in PowerShell on Windows.

## Before you start

1. Clone the repository, and run `uv sync` in it.
2. Activate the environment, so that `pprl` runs as a command: `source .venv/bin/activate` on macOS or Linux, or `.venv\Scripts\Activate.ps1` in PowerShell. Activation lasts for the terminal session, across `cd`. Activate again in each new terminal you open.
3. Create the folder `build/walkthrough/` inside your clone (git ignores `build/`). Every path below is relative to `build/walkthrough/`.

## Step 1. Coordinator: creates the workspace

> **Guide:** coordinator guide, step 2.

The coordinator's workspace is a new folder holding the project's settings, in a file called `project.yml`.

From `build/walkthrough`, run:

```text
mkdir coordinator
```

Create `coordinator/project.yml`:

```yaml
name: Walkthrough project
id: person_id
sites: [site_a, site_b]
fields:
  name: 0.45
  city: 0.10
  state: {weight: 0.05, type: state}
  zip: {weight: 0.15, type: zip}
  dob: {weight: 0.25, type: date}
hash_length: 1024
fill_ratio: 1.0
threshold: 0.90
secret_check: true
tables:
  observation_period:
    primary_key: [person_id]
    keep:
      - not_empty: observation_period_start_date
      - not_empty: observation_period_end_date
    censor: [observation_period_id]
    column:
      observation_period_id: {drop: true}
      observation_period_start_date:
        rank_by: [{take_earliest: {column: observation_period_start_date}}]
      observation_period_end_date:
        rank_by: [{take_latest: {column: observation_period_end_date}}]
      period_type_concept_id: {tether: observation_period_end_date}
```

What these settings mean:

- **`id`** names the column in each site's exports that holds the site's own patient id.
- **`fields`** are the identifying details to hash, each with a weight. The fields share the hash's bits in proportion to their weights; in total they set `fill_ratio` × `hash_length` bits.
- **`secret_check`** makes `link` check that every site used the same secret. Leave it `true` unless the project uses `record_salt`.
- **`hash_length`, `fill_ratio` and `threshold`**, along with the weights, are just example values here.
- **`tables`** describes each clinical table the sites send. Each site's table must have exactly the columns listed under `column`, plus the primary key.
  - **`keep`** says which rows belong in the study: here, periods with both a start and an end date.
  - **`censor`** lists columns that each site empties before anything leaves the site. `observation_period_id` is a site's own number, meaningless anywhere else.
  - **`column`** tells the merge agent how to combine a person's rows from different sites. The merged period covers every site's: the earliest start date and the latest end date. `period_type_concept_id` is tethered to the end date, so it comes from the same row. `observation_period_id` is dropped from the merged table. The order of the rules sets the order of the merged table's columns.

In `coordinator`, run:

```text
pprl init
```

Because the folder holds `project.yml` and no bundle, `init` makes it the coordinator's workspace for a new project.

## Step 2. Coordinator: generates synthetic data

> **Guide:** coordinator guide, step 3.

Next, the coordinator generates synthetic patient data, standing in for what each site would export, described in `synth.yml`.

Create `coordinator/synth.yml`:

```yaml
seed: 2026
sites: [site_a, site_b]
outputs: [omop, patients]
generated_individuals:
  - sites: [site_a, site_b]
    count: 100
  - sites: [site_a]
    count: 900
  - sites: [site_b]
    count: 900
pii:
  name:
    typos: {rate: 0.05, max_edits: 1}
```

This scenario creates 1,900 people:

- 100 with records at both sites
- 900 more at site_a only
- 900 more at site_b only

So each site has 1,000 patients, of whom only 100 are at the other site too. Some names get a typo, as real records do. The fixed `seed` means you get the same people every time.

In `coordinator`, run:

```text
pprl synth
```

`synth` reads `synth.yml` and writes a folder for each site: `coordinator/synth/site_a/` and `coordinator/synth/site_b/`. Each holds the site's patient file, `patients.csv`, and the site's OMOP tables, in `OMOP/`. They stand in for each site's exports. This project uses only `OMOP/observation_period.csv`; leave the other tables where they are.

A person shared by both sites has the same `person_id` at both, which would not apply to a real project. Here, it lets you check the linkage against the truth.

## Step 3. Coordinator: writes bundles and guides

> **Guide:** coordinator guide, step 3.

Each role follows a guide written for this project, covering what to install, which files to export or expect, which commands to run, and what to send to whom, and when. The guides are generated from `project.yml`, plus a second file describing how the project runs: contacts and planned times. `contacts` names one person for each role, including each site participant; wherever a guide says to send a file, it names the person who receives it. Questions always go to the coordinator. `times` fills in the schedule in each guide, and the planned time of each step: no command reads them, so they never stop a step from running early or late. The times are planned in Eastern time, and pprl does not convert them to any other time zone.

Create `coordinator/deployment.yml`:

```yaml
contacts:
  coordinator: {name: Jo Example, email: jo@example.org}
  site_a: {name: Ana Example, email: ana@example.org}
  site_b: {name: Ben Example, email: ben@example.org}
  linkage: {name: Lee Example, email: lee@example.org}
  merge: {name: Max Example, email: max@example.org}
  host: {name: Hal Example, email: hal@example.org}
times:
  bundles: 2026-11-16 09:00
  secrets: 2026-11-16 09:30
  hash: 2026-11-16 12:00
  link: 2026-11-16 14:00
  review: 2026-11-16 14:30
  pack: 2026-11-16 15:00
  merge: 2026-11-16 16:30
  destroy: 2026-11-16 17:00
```

In `coordinator`, run:

```text
pprl bundles
pprl guides
```

- `bundles` writes each role's bundle: `coordinator/outbox/site_a/site_a.pprl`, `coordinator/outbox/site_b/site_b.pprl`, `coordinator/outbox/linkage/linkage.pprl` and `coordinator/outbox/merge/merge.pprl`. None of them contains the secret.
- `guides` writes a guide for each recipient next to its bundle, such as `coordinator/outbox/site_a/site_a.guide.html`. The host, who receives the merged tables, gets a guide and no bundle. The coordinator's own guide, `coordinator/coordinator.guide.html`, is never sent. Open the guides in a browser: each one lists exactly the files and commands that its role uses in the steps below.

A workspace's `outbox/` folder only ever holds files that its role sends to another role, in one folder per recipient.

## Step 4. Transfer: coordinator → every role

> **Guide:** coordinator guide, step 4.

The coordinator sends each role exactly the files in the role's outbox folder: the bundle and the guide. Each role saves them in a new, empty folder: the workspace-to-be.

The host's guide stays in `coordinator/outbox/host/`. The host isn't a pprl workspace, so this walkthrough has no folder for it. Open the host guide there to read along.

From `build/walkthrough`, run:

```text
mkdir site_a
mkdir site_b
mkdir linkage
mkdir merge
cp coordinator/outbox/site_a/* site_a/
cp coordinator/outbox/site_b/* site_b/
cp coordinator/outbox/linkage/* linkage/
cp coordinator/outbox/merge/* merge/
```

## Step 5. site_a participant: creates the workspace

> **Guide:** site guide, step 2.

In `site_a`, run:

```text
pprl init
```

`init` reads the role and the site's name from the bundle, makes the folder a workspace, and lists the files it created. Among them is a key pair, which is used to deliver the project's secret safely:

- `site_a/site_a.private_key.pprl`: the private key, which never leaves the workspace
- `site_a/outbox/coordinator/site_a.public_key.pprl`: the public key, to send to the coordinator

## Step 6. Transfer: site_a participant → coordinator

> **Guide:** site guide, step 3.

The sites hash with a shared secret, which must reach each site participant without anyone else being able to read it. So the site_a participant sends the site's public key to the coordinator over a secure channel, and the coordinator accepts it only from site_a's known contact.

From `build/walkthrough`, run:

```text
cp site_a/outbox/coordinator/site_a.public_key.pprl coordinator/
```

## Step 7. site_b participant: creates the workspace

> **Guide:** site guide, step 2.

In `site_b`, run:

```text
pprl init
```

## Step 8. Transfer: site_b participant → coordinator

> **Guide:** site guide, step 3.

From `build/walkthrough`, run:

```text
cp site_b/outbox/coordinator/site_b.public_key.pprl coordinator/
```

## Step 9. Linkage agent: creates the workspace

> **Guide:** linkage guide, step 2.

The linkage agent makes the workspace as soon as the bundle arrives, ready for the sites' hashes.

In `linkage`, run:

```text
pprl init
```

## Step 10. Merge agent: creates the workspace

> **Guide:** merge guide, step 2.

The merge agent does the same. The merge agent runs nothing else until the go-ahead (step 25).

In `merge`, run:

```text
pprl init
```

## Step 11. Coordinator: makes the secret files

> **Guide:** coordinator guide, step 5.

In `coordinator`, run:

```text
pprl secrets
```

`secrets` creates the project's single secret, locks a copy to each site's public key, and writes `coordinator/outbox/site_a/site_a.secret.pprl` and `coordinator/outbox/site_b/site_b.secret.pprl`. It keeps no copy of the secret, so only each site can unlock that site's file. The coordinator sends each site participant that site's file over a secure channel.

## Step 12. Transfer: coordinator → site participants

> **Guide:** coordinator guide, step 5.

From `build/walkthrough`, run:

```text
cp coordinator/outbox/site_a/site_a.secret.pprl site_a/
cp coordinator/outbox/site_b/site_b.secret.pprl site_b/
```

## Step 13. Transfer: site_a data team → site_a participant

> **Guide:** site guide, steps 4 and 6.

The site_a data team exports the patient file and the clinical tables that the guide asks for, keyed by the site's own `person_id`s. The site participant saves the patient file in the workspace as `patients.csv`, and each table in the workspace's `tables` folder. Here, the synthetic files from step 2 stand in for the exports.

From `build/walkthrough`, run:

```text
cp coordinator/synth/site_a/patients.csv site_a/
mkdir site_a/tables
cp coordinator/synth/site_a/OMOP/observation_period.csv site_a/tables/
```

## Step 14. site_a participant: selects, cleans, hashes and packs

> **Guide:** site guide, steps 5, 7, 8 and 9.

In `site_a`, run:

```text
pprl select
pprl clean
pprl hash
pprl pack
```

What each command did:

- **`select`** decides who is in the study, so it comes first.
  - It checks that each table has exactly the columns the bundle names, and that no two rows share a primary key.
  - It keeps only the rows the project's `keep` rules allow, and reports how many rows each rule left out. Here, it keeps all 1,000 rows: every synthetic period has both dates.
  - It empties the censored column, `observation_period_id`.
  - It writes the kept rows to `site_a.selected.pprl`, which never leaves the workspace.

  The patients with a kept row are the study's patients. Only they will be cleaned and hashed.
- **`clean`** standardizes the patient file.
  - It leaves out patients who aren't in the study (here, none).
  - It replaces each `person_id` with a `study_id`: a random shuffle of 1 to 1,000, drawn separately at each site.
  - It writes `site_a.clean.pprl`, holding the cleaned rows ready to hash and the table pairing each `person_id` with its `study_id`. This file never leaves the workspace.
  - It also writes `site_a.set_aside.csv`, listing rows it couldn't use (here, none). It's the one file in the workspace to open.
- **`hash`** unlocks the secret with the site's private key, and writes `outbox/linkage/site_a.hashes.pprl`: each of the 1,000 records' `study_id` and its hash, and nothing else.
- **`pack`** re-checks the rows `select` kept against the project's rules, and writes them to `outbox/merge/site_a.tables.pprl`, with each `person_id` replaced by its `study_id` from `clean`. This way, no site's own ids ever leave the site.

The site participant packs now, before anything leaves the site, so that any problem with the site's patients or tables comes to light first.

All of these files are pprl files. To see what one holds, run `pprl inspect` on it.

## Step 15. Transfer: site_a participant → linkage

> **Guide:** site guide, step 10.

The site_a participant sends the hashes to the linkage agent, and **holds back the site's tables until the go-ahead** (step 25), though they are already packed.

From `build/walkthrough`, run:

```text
cp site_a/outbox/linkage/site_a.hashes.pprl linkage/
```

## Step 16. Transfer: site_b data team → site_b participant

> **Guide:** site guide, steps 4 and 6.

From `build/walkthrough`, run:

```text
cp coordinator/synth/site_b/patients.csv site_b/
mkdir site_b/tables
cp coordinator/synth/site_b/OMOP/observation_period.csv site_b/tables/
```

## Step 17. site_b participant: selects, cleans, hashes and packs

> **Guide:** site guide, steps 5, 7, 8 and 9.

In `site_b`, run:

```text
pprl select
pprl clean
pprl hash
pprl pack
```

As at site_a, `select` keeps all 1,000 rows, `clean` sets none aside, `hash` writes 1,000 hashes, and `pack` writes the tables.

## Step 18. Transfer: site_b participant → linkage

> **Guide:** site guide, step 10.

From `build/walkthrough`, run:

```text
cp site_b/outbox/linkage/site_b.hashes.pprl linkage/
```

## Step 19. Linkage agent: links

> **Guide:** linkage guide, step 3.

In `linkage`, run:

```text
pprl link
pprl inspect outbox/merge/linkages.pprl
```

`link` compares the two sites' hashes and writes `outbox/merge/linkages.pprl`: one row per linked record, giving its group, its site and its `study_id`. It links 100 pairs, one record from each site, so the file has 200 rows: every one of the 100 people at both sites. It also estimates how many of its pairs are false, here none.

`inspect` shows what the linkage file holds, and which hash files it was made from.

`link` also writes `outbox/coordinator/linkage_report.pprl`, the linkage report: the same-secret check, the count of linked records for each pair of sites, the estimate of false pairs, the histogram of best scores, and any warnings. It holds counts only, never a record or a hash. Run `pprl inspect` on it to see what it holds.

## Step 20. Transfer: linkage → merge and coordinator

> **Guide:** linkage guide, step 4.

The linkage agent sends the linkage file to the merge agent, and the linkage report to the coordinator. The merge agent keeps the linkage file unopened until the go-ahead.

From `build/walkthrough`, run:

```text
cp linkage/outbox/merge/linkages.pprl merge/
cp linkage/outbox/coordinator/linkage_report.pprl coordinator/
```

## Step 21. Coordinator: reviews the linkage

> **Guide:** coordinator guide, step 6.

In `coordinator`, run:

```text
pprl review
```

```text
[ OK ] Same secret: site_a-site_b, bit correlation 0.99.
[NOTE] Linked pairs: site_a-site_b 100 (site_a 10.0%, site_b 10.0%).
[NOTE] Best scores at each other site, 2,000 (every record):
       below 0.85             1,800   90.0%
       0.85-0.90 near miss        0    0.0%
       0.90-0.95 weak            12    0.6%
       0.95 and up              188    9.4%
[NOTE] False pairs, estimated: site_a-site_b 0 (95% range 0-22).
[ OK ] Warnings from link: none.
[ OK ] Wrote linkage_report.svg.
```

`review` reads `linkage_report.pprl`, summarizes it as a checklist, and saves a histogram of the best scores beside it, as `linkage_report.svg`. The coordinator uses these to judge whether the linkage looks plausible. Here:

- both sites used the same secret
- 100 linked pairs, 10% of each site
- 1,800 best scores below the band: the 900 records at each site with no partner
- 200 above the threshold: the 100 pairs, seen from each side
- no near misses
- an estimated 0 false pairs

The split of scores above the threshold may differ a little from run to run, since each run's secret is new.

The coordinator's workspace still holds `outbox/linkage/linkage.pprl`, so `review` also checks the bundle set.

## Step 22. Linkage agent: destroys the workspace

> **Guide:** linkage guide, step 5.

Once the coordinator says the linkage is plausible, the linkage agent's job is done. The linkage file has already gone to the merge agent, so the linkage agent destroys the workspace, hashes and all.

In `linkage`, run:

```text
pprl destroy
```

`destroy` lists every file in the workspace, and asks you to type `yes`. It then deletes every file, leaving only a receipt in its outbox, `linkage/outbox/coordinator/linkage.receipt_<date>.txt`. The receipt lists what was deleted, and holds the workspace's log.

## Step 23. Transfer: linkage → coordinator

> **Guide:** linkage guide, step 5.

The linkage agent sends the receipt to the coordinator. The copy command below uses `*` in place of the date.

From `build/walkthrough`, run:

```text
cp linkage/outbox/coordinator/linkage.receipt_*.txt coordinator/
```

## Step 24. Coordinator: reads the receipt

> **Guide:** coordinator guide, step 6.

The coordinator saves the receipt in the coordinator workspace, like every file received, and reads it: does anything in it, such as its log, suggest a usability problem or a pprl bug worth reporting?

The coordinator doesn't keep the receipts after the project: the coordinator's own `pprl destroy`, in step 34, deletes them with everything else.

## Step 25. Coordinator: gives the go-ahead

> **Guide:** coordinator guide, step 7.

With the linkage reviewed and the linkage agent's receipt read, the coordinator tells each site participant and the merge agent to go ahead. There is no command for this; in a real project, it's a message. Site participants may now send the tables, and the merge agent may start merging.

Site participants keep everything else in the site workspaces, the secret and the hashes included, until the end of the project.

## Step 26. Transfer: site participants → merge

> **Guide:** site guide, step 11.

From `build/walkthrough`, run:

```text
cp site_a/outbox/merge/site_a.tables.pprl merge/
cp site_b/outbox/merge/site_b.tables.pprl merge/
```

## Step 27. Merge agent: merges

> **Guide:** merge guide, step 3.

In `merge`, run:

```text
pprl merge
```

First, `merge` checks that each site's tables and the linkage file came from the same run of `clean`. Then it writes `outbox/host/merged/observation_period.csv`, with one row per person:

- Each person gets a new `person_id`, a random shuffle of 1 to the number of people, so neither the sites' ids nor the `study_id`s remain.
- The 100 linked people each get a single row, combining their two sites' periods.
- The 1,800 records not in the linkage file (each site's 900 unshared people) each become a person of their own.
- In all, 2,000 site rows become 1,900 merged rows.

`merge` also reports, for each column, how many merged rows had different values at the two sites. Here, there are none. With no clinical events, `synth` gives each person a single one-day period, on their date of birth, at both sites. So the earliest-start and latest-end rules have nothing to choose between. It's a useful check, though: every linked pair agreeing on a date of birth is more evidence that the links are true matches.

Now open `merge/merge.log`. It holds every run in this workspace since `init`, line for line as the terminal showed it, with two differences:

- the folder's name, and anything else you typed, are written as `<withheld>`
- technical details, marked `[INFO]`, help the coordinator find what went wrong if there's a problem

## Step 28. Transfer: merge → host

> **Guide:** merge guide, step 4.

The merge agent sends the merged tables, the folder `merge/outbox/host/merged/`, to the host, who keeps them for the study's analysts. The host has no folder in this walkthrough, so there's nothing to copy.

## Step 29. Host: confirms the merged dataset

> **Guide:** host guide, step 2.

The host checks that every table in the host guide has arrived, then tells the coordinator. There's nothing to run. The coordinator waits for this before the next step.

## Step 30. Coordinator: tells the roles to destroy the workspaces

> **Guide:** coordinator guide, step 8.

Once the host confirms receipt of the merged dataset, the coordinator tells each site participant and the merge agent that the project's transfers are complete, and that the workspaces may be destroyed. No role destroys a workspace before this. There is no command for this; in a real project, it's a message.

## Step 31. site_a participant: destroys the workspace and sends the receipt

> **Guide:** site guide, step 12.

In `site_a`, run:

```text
pprl destroy
```

This deletes every file in the workspace, the hashes, the secret file and the private key included. Then the site_a participant sends the receipt to the coordinator.

From `build/walkthrough`, run:

```text
cp site_a/outbox/coordinator/site_a.receipt_*.txt coordinator/
```

The coordinator reads the receipt, as in step 24.

## Step 32. site_b participant: destroys the workspace and sends the receipt

> **Guide:** site guide, step 12.

In `site_b`, run:

```text
pprl destroy
```

From `build/walkthrough`, run:

```text
cp site_b/outbox/coordinator/site_b.receipt_*.txt coordinator/
```

The coordinator reads the receipt, as in step 24.

## Step 33. Merge agent: destroys the workspace and sends the receipt

> **Guide:** merge guide, step 5.

In `merge`, run:

```text
pprl destroy
```

From `build/walkthrough`, run:

```text
cp merge/outbox/coordinator/merge.receipt_*.txt coordinator/
```

The coordinator reads the receipt, as in step 24.

## Step 34. Coordinator: destroys the workspace

> **Guide:** coordinator guide, step 8.

With every receipt read, the coordinator destroys the coordinator workspace.

In `coordinator`, run:

```text
pprl destroy
```

This deletes the receipts the coordinator received, along with everything else, and leaves only the coordinator's receipt, `coordinator/outbox/coordinator/coordinator.receipt_<date>.txt`.

Every workspace now holds only its receipt. In a real project, the only copy of the data left is the host's merged table: the study's dataset. Here, you can delete `build/walkthrough/` when you're done.

## Next steps

`make demo` (or `uv run python -m tests.playground demo`) runs a project like this one automatically, and `make try` (or `uv run python -m tests.playground list`) lists individual usability scenarios to run. The README lists various commands you can run.
