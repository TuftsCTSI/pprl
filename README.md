# TuftsCTSI/pprl

[![CI](https://github.com/TuftsCTSI/pprl/actions/workflows/run-pytest-suite.yml/badge.svg)](https://github.com/TuftsCTSI/pprl/actions/workflows/run-pytest-suite.yml)

This software is used to perform privacy-preserving record linkage (PPRL) to determine whether the same individual has records across different datasets without sharing any patient identifiers:

1. Each participating site (a hospital or health system) hashes patient identifiers. Similar records yield similar hashes, but the hashes can't be converted back to identifiers.
2. Hashes are sent only to a linkage agent, who compares the hashes to detect any likely matches.
3. Clinical data and linkages are sent to the merge agent, who produces a consolidated dataset.
4. A designated host receives the merged dataset.

## Quick start

**If you were sent a guide for a project:** follow the guide. It covers everything your role needs, from installing pprl to the end of the project, so you don't need to read anything here. If something in the guide is missing or unclear, please contact the coordinator.

**If you want to test out the software:**

1. Clone the repository and install it (see [Developing](#developing) below).
2. Run `make` to list the demonstrations and how to run them. Without make, run the equivalent commands listed under [Commands](#commands).
   - `make demo` simulates a complete project, playing out each role.
   - `make try` lists smaller scenarios, many containing deliberate mistakes that pprl must catch.
3. After a demonstration runs, you can look at its input and output files as examples.
4. For a guided tour, follow [docs/walkthrough.md](docs/walkthrough.md). You play every role in a small project on made-up data, in less than half an hour.

**If you are coordinating a new project:** put a `project.yml` in a new folder and run `pprl init` there. Then `pprl bundles` writes each role's settings, and `pprl guides` writes each role's guide. The walkthrough shows each of these steps.

## Installing

pprl can be installed in two ways:

| | Wheel install | Source install |
| --- | --- | --- |
| **For** | Anyone following a project guide | Developers, and anyone exploring how pprl works |
| **How** | The `uv tool install` command in your guide | Clone this repository and run `uv sync` (see [Developing](#developing)) |
| **Runs as** | `pprl` | `uv run pprl`, from the clone |
| **Includes** | Every `pprl` command, for every role | Every `pprl` command, plus the test suite, demonstrations, walkthrough, examples, benchmarks and maintenance tasks |

The wheel is published only as an asset of each [GitHub release](https://github.com/TuftsCTSI/pprl/releases). This software is not published on PyPI, so **do not run** `pip install pprl`. Python 3.14 is required, which uv installs if needed.

### Installing the wheel without uv

If you can't install uv, you can install the wheel with pip instead. You need Python 3.14 already installed: unlike uv, pip doesn't install Python for you, and many Linux distributions ship an older version. You also need access to github.com, because some of pprl's dependencies are downloaded from there.

In the commands below, replace `WHEEL` with the wheel's address from your guide's `uv tool install` command, such as `https://github.com/TuftsCTSI/pprl/releases/download/v3.0.0/pprl-3.0.0-py3-none-any.whl`, or with the path of a wheel you downloaded.

macOS or Linux:

```bash
python3.14 -m venv ~/.venvs/pprl
~/.venvs/pprl/bin/pip install WHEEL
~/.venvs/pprl/bin/pprl --help
```

Windows (PowerShell):

```powershell
py -3.14 -m venv $HOME\venvs\pprl
& $HOME\venvs\pprl\Scripts\pip install WHEEL
& $HOME\venvs\pprl\Scripts\pprl --help
```

This installs pprl into a virtual environment of its own. To run it as just `pprl`, add the environment's `bin` folder (`Scripts` on Windows) to your PATH. On macOS or Linux, you can instead link the command into `~/.local/bin`:

```bash
mkdir -p ~/.local/bin && ln -s ~/.venvs/pprl/bin/pprl ~/.local/bin/pprl
```

To upgrade, run the `pip install` command again with the new wheel. To uninstall, delete the environment's folder.

If you have [pipx](https://pipx.pypa.io/), it does all of this in one command: `pipx install --python python3.14 WHEEL`.

## Developing

1. Install [uv](https://docs.astral.sh/uv/), a Python package manager:
    * macOS or Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
    * Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. Clone the repository, and install pprl and its development tools into `.venv`:

   ```bash
   git clone https://github.com/TuftsCTSI/pprl.git
   cd pprl
   uv sync
   ```

3. Run `make` to list the demonstrations, and how to configure and run them.
4. Run `make test` to run the test suite.

### Commands

Each `make` target runs a command that you can also run directly, from the root of your clone, on any system:

| make | Direct command |
| --- | --- |
| `make test` | `uv run pytest` |
| `make test-all` | `uv run pytest -m "extended or not extended"` |
| `make demo` | `uv run python -m tests.playground demo` |
| `make demo SIZE=medium MESSY=1 KEEP=1` | `uv run python -m tests.playground demo --size medium --messy --keep` |
| `make try` | `uv run python -m tests.playground list` |
| `make try COMMAND=hash` | `uv run python -m tests.playground list hash` |
| `make try CASE=hash/12` | `uv run python -m tests.playground try hash/12` |
| `make reset` | `uv run python -m tests.playground reset` |
| `make docs` | `uv run python -m tests.usability_cases` |
| `make pins` | `uv run --frozen python -m tests.pins` |
| `make pins-upgrade` | `uv run --frozen python -m tests.pins --upgrade` |
| `make audit` | `uv audit --preview-features audit-command` |

`make update-fixtures`, `make bench` and `make clean` are available only through make; see the `Makefile` for what each one runs.

## Getting help

When pprl finds a problem, its message says what is wrong and how to fix it. If the fix is unclear, or pprl stops unexpectedly, send your log from your workspace folder (named after your role, such as `site_a.log`) to the project's coordinator, named in your guide, over a secure channel. The log never contains patient information or the project's secret, but please review it before sending it.

Please ask about anything that is unclear, and report any problem, however small: a step in your guide you weren't sure about, a message you didn't expect, or anything that didn't seem right. Contact the coordinator for questions regarding the current project, or [open a public GitHub issue](https://github.com/TuftsCTSI/pprl/issues) for general questions/suggestions.
