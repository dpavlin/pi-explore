# pi-explore

`pi-explore` is an advanced terminal-based conversation transcript explorer and session management utility designed for the **Pi coding agent** ([`@earendil-works/pi-coding-agent`](https://github.com/earendil-works/pi-coding-agent)).

It aggregates telemetry, active time spans, workspace directories, tool executions, token and cost accounting, affected workspace files, and prompt contexts across all your Pi agent sessions, providing a data-dense, color-coded interactive visualization in your terminal.

---

## Key Features

- 👤 **Prompt Identity Mapping:** Parses and displays customizable subsets of first and last user prompts in full uncropped detail to quickly identify what the session was about (using `--first` and `--last`).
- 🧭 **Workspace & Session Tree Discovery:** Automatically decodes and resolves encoded session directories (`--home-dpavlin-aimax--` &rarr; `/home/dpavlin/aimax`) and maps session UUIDs to their active project directories.
- 💰 **Exact Token & Cost Accounting:** Sums input, output, cache-read, cache-write tokens, and dollar costs recorded natively by Pi in every assistant turn.
- 🛠️ **Detailed Tool & File Breakdown:** Counts tool executions (`bash`, `read`, `write`, `edit`, `browser_execute`, etc.) and pinpoints workspace files modified or created during each session.
- 🌿 **Compaction & Branch Awareness:** Traces append-only session trees (`id`, `parentId`, `leaf`) along the active trajectory by default, while supporting complete exploration of abandoned branches (`--all-entries`).
- 🔍 **Strict Grep Engine with Greedy Snippet Selection:** Multi-word logical AND search with word-boundary matching (`\b`), turn-restricted filtering (`--turn`), full-field search (`--search-all`), and greedy snippet coverage.
- 📜 **Full Color-Coded Transcript Streaming:** Reads full session logs into terminal or pager (`less -R`) with assistant internal thoughts, tool call payloads, and execution duration telemetry.
- 🔄 **CLI Resume Integration:** Generates exact commands to instantly resume any past session via `pi --session <uuid>` or `pi --continue`.
- 🌐 **HTML Export Integration:** Detects existing session HTML exports and triggers HTML generation via `pi --export`.

---

## Installation

Symlink or copy `pi-explore.py` into your user bin directory (e.g. `~/.local/bin/`) and ensure it is executable:

```bash
# Symlink for live updates from git repository:
ln -s /home/dpavlin/pi-explore/pi-explore.py ~/.local/bin/pi-explore

# Or copy directly:
cp /home/dpavlin/pi-explore/pi-explore.py ~/.local/bin/pi-explore
chmod +x ~/.local/bin/pi-explore
```

Now you can type `pi-` in your terminal and press `<TAB>` for automatic tab completion!

---

## Command Usage

### 1. Catalog Sessions for Current Directory
Displays a data-dense structured overview of past Pi conversations for the current workspace:
```bash
pi-explore
```

### 2. Catalog All Sessions Across All Workspaces
Lists past sessions globally across all project directories:
```bash
pi-explore -a
# or: pi-explore --all
```

### 3. Display Custom Prompt Subsets in Full
Displays sessions with the first 2 and last 2 user prompts uncropped:
```bash
pi-explore -a --first 2 --last 2
```

### 4. Search Sessions with Grep Filter
Finds sessions containing all specified words case-insensitively:
```bash
# Global search:
pi-explore -a --grep mailman migration

# Restrict matches to within a single user-assistant turn:
pi-explore -a --grep nginx ssl --turn

# Search all fields including internal thoughts and tool command lines:
pi-explore -a --grep "apt-get purge" --search-all
```

### 5. Stream a Conversation Log to `less`
Reads a specific session log with terminal color formatting:
```bash
# By session UUID or partial prefix:
pi-explore 01a11aa2 | less -R

# By direct file path:
pi-explore ~/.pi/agent/sessions/--home-dpavlin-ffzg-infra--/2026-10-08T08-30-44-823Z_01a11aa2-e256-736e-ad11-d1462063bcd5.jsonl | less -R
```

### 6. Export Session to HTML
Generates a standalone HTML export using Pi's built-in exporter:
```bash
pi-explore --export session.html 01a11aa2
```

---

## CLI Options Reference

| Option | Description |
|---|---|
| `-a`, `--all` | List all sessions across all workspaces (default filters to current working directory). |
| `-d`, `--debug` | Enable detailed real-time diagnostic telemetry on `sys.stderr`. |
| `-v`, `--verbose` | Increase verbosity level (`-v`: tool breakdown and modified file stats; `-vv`: prints full text content of modified files). |
| `--grep <words>` | Filter sessions containing all specified query words (case-insensitive AND match). |
| `--turn` | Restrict `--grep` search to match all words within a single user-assistant turn. |
| `--search-all` | Search all fields (assistant thoughts, tool arguments, tool outputs) instead of just dialogue. |
| `--first <N>` | Number of first user prompts to display (default: `1`). |
| `--last <M>` | Number of last user prompts to display (default: `1`). |
| `--no-color` | Disable ANSI terminal color escape codes. |
| `--no-thoughts` | Exclude assistant internal reasoning blocks in transcript view. |
| `--no-tools` | Exclude tool call requests and tool results in transcript view. |
| `--all-entries` | Show all entries including abandoned branch forks (default shows active leaf branch). |
| `--session-dir <dir>` | Override session root storage directory (default: `~/.pi/agent/sessions`). |
| `--export [out.html]` | Export session to HTML using `pi --export`. |
| `-h`, `--help` | Show CLI help manual. |

---

## Technical Details & Architecture

- **Session Format:** Detailed specification of Pi's append-only tree format and JSONL schema in [docs/session_architecture.md](docs/session_architecture.md).
- **Grep Matching Engine:** Detailed specification of word-boundary filtering and greedy snippet coverage logic in [docs/grep_matching.md](docs/grep_matching.md).
- **Session Storage Location:** Defaults to `~/.pi/agent/sessions/` (configurable via `$PI_CODING_AGENT_SESSION_DIR` or `$PI_CODING_AGENT_DIR/sessions`).
- **Binary Integration:** Integrates natively with `pi` (`~/.local/bin/pi -> ~/.pi/agent/bin/pi`).
