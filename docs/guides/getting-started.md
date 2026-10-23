# Getting started

This guide takes you from a fresh clone to a healthy install on synthetic data, then to an empty folder for your own
records. It assumes nothing about your search. Allow about 15 minutes for the demo and then as long as you want for
your own data.

## What you need

- macOS or Linux. Windows has not been tried (see the [FAQ](../FAQ.md)).
- Python 3.11 or newer (`python3 --version`).
- [Claude Code](https://claude.com/claude-code), installed and signed in.
- `git`.
- Recommended: [`uv`](https://docs.astral.sh/uv/), which starts the records MCP server. Without it everything else
  works and the MCP server does not start.

Python modules: `pyyaml` and `requests`. The installer offers to install them with `pip --user` and asks first
(`--yes` answers for you). Gmail, Calendar, resume rendering and PDF checks need optional extras listed in
`requirements-optional.txt`; install them only when you use the feature.

## 1. Clone and try the demo

```bash
git clone <repo-url> ~/jaja
cd ~/jaja
./install.sh --demo --yes
```

`install.sh --help` lists the options. With `--demo` the installer:

1. checks Python and notes whether `uv` is present;
2. checks `pyyaml` and `requests`;
3. creates a data folder at `~/jaja-demo` filled with synthetic records (a fictional candidate, Sam Rivera;
   fictional employers) using `tools/init-data.py --demo --set-config`;
4. writes `data_root:` into `~/.config/jaja/config.yaml` so scripts find that folder (other keys in the file are
   kept);
5. installs a git pre-commit leak guard in this checkout, unless you pass `--no-hook`;
6. prints the two plugin commands;
7. runs `scripts/doctor.py` and exits with its status.

Nothing is real and nothing is sent. The installer never overwrites a file, so it is safe to run again.

Note that step 4 changes a file outside the repository. If you would rather not point your configuration at the demo,
use the copy-to-a-temporary-folder route in the [demo walkthrough](demo-walkthrough.md), which uses environment
variables only.

## 2. Install the plugin

The installer prints these; run them yourself:

```bash
claude plugin marketplace add ./
claude plugin install jaja@jaja --scope user
```

`./` is the repository root; run the first command from there. Check the result with `claude plugin list`; the plugin
should appear with its status. A plugin that fails to load is the usual reason a skill is missing.

Skills are slash commands under the plugin's namespace, written `/jaja:<skill>` in these documents, for example
`/jaja:doctor`. Agents are called by name (`fit-scorer`, `resume-tailor`) or work inside a lane.

## 3. Start a session in the demo folder

```bash
cd ~/jaja-demo
claude
```

Start the session in the data folder; that is where the records are. Try `/jaja:daily-job-plan`, then
`/jaja:score-fit` or `/jaja:research-company`. The step-by-step commands, with output, are in the
[demo walkthrough](demo-walkthrough.md).

## 4. Check health

```bash
python3 scripts/doctor.py
```

Run from the repository, with the data folder resolved from the config file. It prints one line per check and a
summary. On a clean machine with the demo data you should see `[ok]` for Python, modules, `uv`, the data root, data
folders, config, profile and evidence ids, records, lint, adapters, hooks, the plugin manifest, the MCP server,
routines and the leak guard. One advisory warning about role cards is expected on the demo data; see the
[roadmap](../ROADMAP.md). `--fix-hints` prints a suggested fix beside each failing line, and `--json` gives
machine-readable output.

## 5. Create your own data folder

```bash
./install.sh --data ~/jaja-data
```

This creates a blank skeleton with `tools/init-data.py` (it never overwrites a file) and points `data_root:` at it. It
contains `profile/`, `targets/`, `config/`, `hub/` and the other folders described in
[REFERENCE.md](../REFERENCE.md). Then:

```bash
cd ~/jaja-data
claude
```

Each time, start the session in the folder you are working in. To point a single command at a different folder
without changing your configuration, set `JAJA_DATA`:

```bash
JAJA_DATA=~/jaja-data python3 ~/jaja/scripts/doctor.py
```

The project used to be called job-agency. Every `JAJA_<NAME>` variable still answers to its earlier name
`JOB_AGENCY_<NAME>` (and `JOB_SEARCH_ROOT` for the data folder), and `~/.config/job-agency` is used while it is the only
config folder that exists. To move your tokens and config to `~/.config/jaja`, run `tools/migrate-config-dir.py`.

## 6. Where to go next

- [Your first search](your-first-search.md): fill in your records and run the first pass.
- [Configuration](configuration.md): identity, style and policy.
- [Connectors](../SETUP-CONNECTORS.md): Gmail, Calendar and others, all optional and off until you set them up.

## If something fails

| Symptom | Likely cause and fix |
|---|---|
| `python3 was not found` or an old version | Install Python 3.11 or newer, then run the installer again. |
| `pip could not install PyYAML` | An externally managed Python. Use a virtual environment or your package manager, as the installer message suggests. |
| doctor: `mcp-server` fails | The check imports the server under `uv run`. Make sure the `uv` line above it is `[ok]`, and that `uv` can fetch the server's dependencies. |
| doctor: `adapters` fails | A card or skill changed without a render. Run `python3 scripts/kernel/sync.py`. |
| A skill is missing in the session | The plugin did not load. Run `claude plugin list` and look at its status. |
| Scripts report `NO-DATA-FOLDER-CONFIGURED` | No data folder is set. Pass `--data`, set `JAJA_DATA`, or rerun the installer. |
