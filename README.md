# Xcalibur

The smallest agent harness that still does real work: a handful of files of plain
Python, standard library only, zero dependencies. It reads, writes, and runs
code inside one directory, asks before risky calls, compacts long runs to fit a
token budget, remembers project facts, loads skills on demand, delegates to
sub-agents, and survives a `kill -9`.

<p align="center"><img src="docs/banner.gif" alt="xcalibur startup animation" width="720"></p>

## Install as a CLI

Needs Python 3.9+ and nothing else.

```sh
git clone https://github.com/shamim23/Xcalibur0.git
cd Xcalibur0
pip install -e .            # or: pipx install -e .   (isolated, puts xcalibur on PATH)
rehash                      # zsh only: pick up the new command in the current shell
xcalibur --version
```

`-e` (editable) means pulling or editing the code takes effect without
reinstalling. If `xcalibur: command not found`, the `pip` you used belongs to a
Python whose `bin/` folder isn't on your `PATH`: run `python3 -m pip install -e .`
with the Python you actually use, or run `python3 -m xcalibur` instead.

Give it your OpenAI key, either in the environment (works from any directory) or
in `xcalibur_key.sh` in the directory you launch from (git-ignored):

```sh
export OPEN_AI_KEY=sk-...                 # add to ~/.zshrc to keep it
export XCALIBUR_MODEL=gpt-5.6-terra       # optional
xcalibur doctor --ping                    # checks python, key, model, workdir; one tiny API call
```

Then `cd` into any project and run `xcalibur`. The agent can only touch files in
that directory (or the one given with `-d`). To remove it: `pip uninstall xcalibur`.

### Interactive

```sh
xcalibur                        # REPL in safe mode (asks before writes and commands)
xcalibur "fix the failing test" # REPL that starts on this task
xcalibur -c                     # continue the latest session here
xcalibur --resume 17948         # resume a session by id prefix
```

In the REPL, lines starting with `/` go to the harness, not the model:

| Command | Does |
|---|---|
| `/help` | list commands |
| `/clear` | start a fresh conversation (old log kept) |
| `/mode [safe\|yolo\|read-only]` · `/model [name]` | show or switch |
| `/compact` | summarize the conversation now to free context |
| `/tokens` | usage for the last run, the session, and current context size |
| `/tools` · `/skills` | what the agent can call or load |
| `/memory [edit]` · `/remember <fact>` | view/edit `XCALIBUR.md`; the prompt reloads |
| `/sessions` · `/resume [id]` | list or switch sessions |
| `/exit` | quit (or Ctrl-D) |

Approval prompts take `y` (once), `a` (always allow this tool for the session)
or anything else for no; the deny list still applies in every case. End a line
with `\` to continue it, or wrap a block in `"""` lines. Ctrl-C interrupts a run
and keeps the log. History lives in `~/.xcalibur_history`; tab completes
commands. `--no-banner` / `XCALIBUR_NO_BANNER=1` skip the startup animation.

### Headless and scripting

```sh
xcalibur -p "add a --verbose flag" -d ./myproject          # yolo mode by default
cat error.log | xcalibur -p "why does this fail?"         # piped stdin is appended
xcalibur -p "list TODOs" --output-format json             # {ok, result, session, model, turns, usage}
xcalibur -p "refactor utils" --output-format stream-json  # one JSON event per line, then a result line
```

In text mode only the final answer goes to stdout; tool activity goes to stderr
when it is a terminal (or with `-v`). Exit codes: `0` ok, `1` runtime/provider
error, `2` usage error, `130` interrupted. Colors are off when output is not a
TTY, with `NO_COLOR`, or with `--no-color`.

### Subcommands

```sh
xcalibur sessions [-d DIR] [--json]          # list sessions, newest first
xcalibur sessions show [ID]                  # print a transcript (latest if no id)
xcalibur fleet jobs.json --workers 4         # run [{name, workdir, task}] in parallel
xcalibur doctor [--ping]                     # environment check
```

Other flags: `-d/--workdir` sets the jail, `-m/--model`, `--mode`,
`--max-turns`, `--budget-tokens`, `--system-append TEXT`. Run the tests with
`python3 -m unittest`.

## Anatomy

| Day | Files | What it teaches |
|-----|-------|-----------------|
| 1 | `provider.py` · `loop.py` | An agent is a while-loop around one model call; the provider hides the vendor's wire format. |
| 2 | `tools.py` · `security.py` | Tools are typed functions jailed to one directory; one policy gate sees every call first. |
| 3 | `context.py` | Compaction swaps old turns for a summary and keeps recent turns verbatim. |
| 4 | `memory.py` · `skills.py` · `session.py` · `subagent.py` | The prompt is assembled from project memory and a skill catalog; an append-only log makes runs durable; a sub-agent is a harness exposed as a tool. |
| 5 | `harness.py` · `__init__.py` · `cli.py` · `ui.py` · `commands.py` · `banner.py` · `fleet.py` · `__main__.py` | Composition: one `Harness` object, a CLI over it (rendering, slash commands, banner), and a thread pool of them. |

State an agent leaves in its working directory:

- `XCALIBUR.md` holds project memory, one bullet per fact. It's added to every system prompt.
- `skills/<name>/SKILL.md` holds skills, listed by their `description:` front-matter line.
- `.xcalibur/sessions/*.jsonl` holds the session logs, one message per line.

## Compose your own

```python
from xcalibur import Harness, Policy, tool

@tool("Return today's date as YYYY-MM-DD.")
def today():
    import datetime
    return datetime.date.today().isoformat()

agent = Harness("./myproject", policy=Policy("safe", lambda call, reason: True),
                extra_tools=[today], system_extra="Date every changelog entry.")
print(agent.run("Add a CHANGELOG.md entry for the new --verbose flag."))
```

Run several agents at once, each in its own directory:

```python
from xcalibur import Harness, run_fleet

results = run_fleet(
    [{"name": "api", "workdir": "./api", "task": "add input validation"},
     {"name": "web", "workdir": "./web", "task": "fix the lint errors"}],
    make_harness=lambda workdir: Harness(workdir))
```
