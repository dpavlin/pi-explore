#!/usr/bin/env python3
"""
pi-explore: Terminal-based conversation transcript explorer and session management utility
for the Pi coding agent (@earendil-works/pi-coding-agent).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from collections import Counter
from datetime import datetime
from pathlib import Path

# ANSI Escape Codes for Terminal Highlighting
CLR_RESET = "\033[0m"
CLR_BOLD = "\033[1m"
CLR_DIM = "\033[2m"
CLR_RED = "\033[31m"
CLR_GREEN = "\033[32m"
CLR_YELLOW = "\033[33m"
CLR_BLUE = "\033[34m"
CLR_MAGENTA = "\033[35m"
CLR_CYAN = "\033[36m"
CLR_WHITE = "\033[37m"

DEBUG_MODE = False

def debug_log(msg: str) -> None:
    """Emits timestamped diagnostic telemetry to sys.stderr if DEBUG_MODE is active."""
    if DEBUG_MODE:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        sys.stderr.write(f"{CLR_DIM}{CLR_YELLOW}[DEBUG {timestamp}] {msg}{CLR_RESET}\n")
        sys.stderr.flush()

def get_default_agent_dir() -> Path:
    """Resolves the Pi agent configuration directory."""
    env_dir = os.environ.get("PI_CODING_AGENT_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return Path("~/.pi/agent").expanduser()

def get_sessions_root(custom_dir: str = None) -> Path:
    """Resolves the root directory where Pi stores session logs."""
    if custom_dir:
        return Path(custom_dir).expanduser()
    env_session_dir = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
    if env_session_dir:
        return Path(env_session_dir).expanduser()
    return get_default_agent_dir() / "sessions"

def encode_cwd(cwd_path: str) -> str:
    """Encodes a workspace path into Pi's directory name format (--path-to-dir--)."""
    resolved = os.path.realpath(cwd_path)
    clean = re.sub(r'^[/\\]', '', resolved)
    encoded = re.sub(r'[/\\:]', '-', clean)
    return f"--{encoded}--"

def format_duration(seconds: float) -> str:
    """Formats duration in seconds into human-readable compact notation."""
    if seconds < 0:
        return "0s"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m {int(seconds % 60)}s"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h {int(minutes % 60)}m"
    days = hours / 24
    return f"{int(days)}d {int(hours % 24)}h"

def format_size(bytes_val: int) -> str:
    """Formats file size in bytes to B, KB, or MB."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    kb = bytes_val / 1024.0
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024.0
    return f"{mb:.1f} MB"

def format_tokens(count: int) -> str:
    """Formats token count compactly (e.g. 1.2k, 14.5k, 1.2M)."""
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        return f"{count / 1000:.1f}k"
    return f"{count / 1_000_000:.2f}M"

def format_cost(cost_usd: float) -> str:
    """Formats token cost in USD."""
    if cost_usd <= 0.0:
        return "$0.00"
    if cost_usd < 0.01:
        return f"${cost_usd:.4f}"
    return f"${cost_usd:.2f}"

def is_text_file(filepath: str) -> bool:
    """Detects whether a file is valid UTF-8 text without null bytes."""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(2048)
        if b'\x00' in chunk:
            return False
        chunk.decode("utf-8", errors="strict")
        return True
    except Exception:
        return False

def highlight_text(text: str, words: list, highlight_color: str = CLR_BOLD + CLR_YELLOW, context_color: str = CLR_WHITE) -> str:
    """Applies ANSI color highlighting to matched words at word boundaries."""
    if not words or not text:
        return text
    try:
        escaped_words = [re.escape(w) for w in words]
        escaped_words.sort(key=len, reverse=True)
        pattern = re.compile(rf"\b({'|'.join(escaped_words)})", re.IGNORECASE)
        replacement = f"{highlight_color}\\1{CLR_RESET}{context_color}"
        return pattern.sub(replacement, text)
    except Exception:
        return text

def parse_iso_timestamp(ts_str: str) -> datetime:
    """Parses ISO timestamp strings into datetime objects."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        try:
            clean = ts_str.replace("Z", "").split("+")[0]
            return datetime.fromisoformat(clean)
        except Exception:
            return None

def extract_message_text(message: dict) -> str:
    """Extracts raw text content from a message structure."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                b_type = block.get("type")
                if b_type == "text":
                    parts.append(block.get("text", ""))
                elif b_type == "toolCall":
                    parts.append(f"toolCall:{block.get('name', '')}")
                elif b_type == "thinking":
                    parts.append(block.get("thinking", ""))
        return " ".join(parts).strip()
    return ""

def load_session_entries(file_path: str) -> list:
    """Reads all raw JSONL entries from a session file."""
    entries = []
    debug_log(f"Loading entries from file: {file_path}")
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                entries.append(data)
            except json.JSONDecodeError as ex:
                debug_log(f"Malformed JSON at line {line_num} in {file_path}: {ex}")
    debug_log(f"Loaded {len(entries)} entries from {file_path}")
    return entries

def build_active_path(entries: list) -> list:
    """Traces the active leaf branch backwards to root using parentId links."""
    if not entries:
        return []
    by_id = {}
    for entry in entries:
        e_id = entry.get("id")
        if e_id:
            by_id[e_id] = entry
    leaf = entries[-1]
    path = []
    current = leaf
    visited = set()
    while current:
        path.append(current)
        c_id = current.get("id")
        if c_id:
            visited.add(c_id)
        parent_id = current.get("parentId")
        if parent_id and parent_id not in visited:
            current = by_id.get(parent_id)
        else:
            break
    path.reverse()
    return path

def extract_searchable_text(entry: dict, search_all: bool = False) -> str:
    """Extracts searchable plain text from an entry based on search options."""
    parts = []
    e_type = entry.get("type")

    if e_type == "message":
        msg = entry.get("message", {})
        role = msg.get("role")
        content = msg.get("content")

        if role == "user":
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        parts.append(b.get("text", ""))
            elif isinstance(content, str):
                parts.append(content)

        elif role == "assistant":
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    b_type = b.get("type")
                    if b_type == "text":
                        parts.append(b.get("text", ""))
                    elif b_type == "thinking" and search_all:
                        parts.append(b.get("thinking", ""))
                    elif b_type == "toolCall" and search_all:
                        parts.append(b.get("name", ""))
                        args = b.get("arguments", {})
                        if args:
                            parts.append(json.dumps(args))
            elif isinstance(content, str):
                parts.append(content)

        elif role == "toolResult" and search_all:
            parts.append(msg.get("toolName", ""))
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        parts.append(b.get("text", ""))

    elif e_type in ("compaction", "branch_summary"):
        summary = entry.get("summary")
        if summary:
            parts.append(summary)

    return " ".join(parts)

def process_session_file(
    file_path: str,
    target_cwd: str = None,
    show_all: bool = False,
    search_words: list = None,
    use_turn_matching: bool = False,
    search_all: bool = False,
    active_branch_only: bool = True
) -> dict:
    """Parses a session JSONL file and extracts metadata, telemetry, and search matches."""
    debug_log(f"Inspecting session file: {file_path}")
    try:
        entries = load_session_entries(file_path)
    except Exception as ex:
        debug_log(f"Failed to load {file_path}: {ex}")
        return None

    if not entries:
        debug_log(f"Empty session file: {file_path}")
        return None

    header = entries[0]
    if header.get("type") != "session":
        debug_log(f"Missing session header at line 1 in {file_path}")
        return None

    session_id = header.get("id", "unknown-id")
    session_cwd = header.get("cwd", "")
    session_version = header.get("version", 0)
    parent_session = header.get("parentSession")
    header_timestamp_str = header.get("timestamp")

    # Match against target workspace directory unless show_all is True
    if not show_all and target_cwd:
        norm_target = os.path.realpath(target_cwd)
        norm_session_cwd = os.path.realpath(session_cwd) if session_cwd else ""
        if norm_session_cwd != norm_target:
            debug_log(f"Skipping session {session_id}: cwd '{norm_session_cwd}' != target '{norm_target}'")
            return None

    stat_info = os.stat(file_path)
    mtime = stat_info.st_mtime
    mtime_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')

    # Detect branches and determine entries to evaluate
    active_path = build_active_path(entries)
    has_branches = len(active_path) < len(entries)
    entries_to_process = active_path if (active_branch_only and has_branches) else entries

    session_name = None
    user_prompts = []
    models_used = set()
    thinking_levels = []
    tool_counts = Counter()
    thought_count = 0
    total_steps = len(entries)
    active_steps = len(entries_to_process)

    token_usage = {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": 0
    }
    cost_total = 0.0

    start_time = parse_iso_timestamp(header_timestamp_str)
    end_time = start_time

    affected_files = {}

    searchable_steps = []

    for idx, entry in enumerate(entries_to_process, 1):
        e_type = entry.get("type")
        e_ts = parse_iso_timestamp(entry.get("timestamp"))
        if e_ts:
            if start_time is None or e_ts < start_time:
                start_time = e_ts
            if end_time is None or e_ts > end_time:
                end_time = e_ts

        if e_type == "session_info":
            name_val = entry.get("name")
            if name_val:
                session_name = name_val.strip()

        elif e_type == "thinking_level_change":
            tl = entry.get("thinkingLevel")
            if tl:
                thinking_levels.append(tl)

        elif e_type == "model_change":
            prov = entry.get("provider", "unknown")
            mid = entry.get("modelId", "unknown")
            models_used.add(f"{prov}/{mid}")

        elif e_type == "compaction":
            details = entry.get("details", {})
            for m_path in details.get("modifiedFiles", []):
                affected_files[m_path] = affected_files.get(m_path, "modified")

        elif e_type == "branch_summary":
            details = entry.get("details", {})
            for m_path in details.get("modifiedFiles", []):
                affected_files[m_path] = affected_files.get(m_path, "modified")

        elif e_type == "message":
            msg = entry.get("message", {})
            role = msg.get("role")
            m_ts_raw = msg.get("timestamp")
            if isinstance(m_ts_raw, (int, float)) and m_ts_raw > 0:
                try:
                    m_ts = datetime.fromtimestamp(m_ts_raw / 1000.0)
                    if end_time is None or m_ts > end_time:
                        end_time = m_ts
                except Exception:
                    pass

            if role == "user":
                content = msg.get("content")
                prompt_txt = ""
                if isinstance(content, list):
                    text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    prompt_txt = " ".join(text_parts).strip()
                elif isinstance(content, str):
                    prompt_txt = content.strip()

                if prompt_txt:
                    clean_prompt = prompt_txt.replace("\n", " ").strip()
                    user_prompts.append(clean_prompt)

            elif role == "assistant":
                m_model = msg.get("model")
                m_prov = msg.get("provider")
                if m_model and m_prov:
                    models_used.add(f"{m_prov}/{m_model}")

                usage = msg.get("usage", {})
                token_usage["input"] += usage.get("input", 0)
                token_usage["output"] += usage.get("output", 0)
                token_usage["cacheRead"] += usage.get("cacheRead", 0)
                token_usage["cacheWrite"] += usage.get("cacheWrite", 0)
                token_usage["totalTokens"] += usage.get("totalTokens", 0)

                cost_obj = usage.get("cost", {})
                cost_total += cost_obj.get("total", 0.0)

                content = msg.get("content", [])
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        b_type = b.get("type")
                        if b_type == "thinking":
                            thought_count += 1
                        elif b_type == "toolCall":
                            t_name = b.get("name", "unknown")
                            tool_counts[t_name] += 1
                            args = b.get("arguments", {})
                            if t_name in ("write", "edit"):
                                p_val = args.get("path")
                                if p_val:
                                    affected_files[p_val] = "written" if t_name == "write" else "edited"

        searchable_text = extract_searchable_text(entry, search_all)
        searchable_steps.append((entry, searchable_text, idx))

    # Duration calculations
    duration_str = "N/A"
    if start_time and end_time:
        diff_sec = (end_time - start_time).total_seconds()
        duration_str = format_duration(diff_sec)

    # Grep Search Evaluation
    is_matched = True
    matched_snippets = []
    total_matches = 0

    if search_words:
        if use_turn_matching:
            turns_text = []
            current_turn = []
            for entry, text, _ in searchable_steps:
                if entry.get("type") == "message" and entry.get("message", {}).get("role") == "user":
                    if current_turn:
                        turns_text.append(" ".join(current_turn))
                        current_turn = []
                current_turn.append(text)
            if current_turn:
                turns_text.append(" ".join(current_turn))

            turn_matched = False
            for t_text in turns_text:
                all_found = True
                for word in search_words:
                    if not re.search(rf"\b{re.escape(word)}", t_text, re.IGNORECASE):
                        all_found = False
                        break
                if all_found:
                    turn_matched = True
                    break
            is_matched = turn_matched
        else:
            session_text = " ".join([text for _, text, _ in searchable_steps])
            for word in search_words:
                if not re.search(rf"\b{re.escape(word)}", session_text, re.IGNORECASE):
                    is_matched = False
                    break

        if is_matched:
            # Extract line candidates for snippet selection
            candidates = []
            for entry, text, step_idx in searchable_steps:
                desc = "Entry"
                e_type = entry.get("type")
                if e_type == "message":
                    role = entry.get("message", {}).get("role")
                    if role == "user":
                        desc = "User Prompt"
                    elif role == "assistant":
                        desc = "Assistant Response"
                    elif role == "toolResult":
                        desc = f"Tool Result ({entry.get('message', {}).get('toolName', 'tool')})"
                elif e_type == "compaction":
                    desc = "Compaction"
                elif e_type == "branch_summary":
                    desc = "Branch Summary"

                lines = text.splitlines()
                for line in lines:
                    line_s = line.strip()
                    if not line_s:
                        continue
                    matched_in_line = {w for w in search_words if re.search(rf"\b{re.escape(w)}", line_s, re.IGNORECASE)}
                    if matched_in_line:
                        total_matches += 1
                        candidates.append({
                            "step_idx": step_idx,
                            "desc": desc,
                            "line": line_s,
                            "matched_words": matched_in_line,
                            "index": len(candidates)
                        })

            # Greedy Coverage Selector (prioritizes covering all query terms, up to 10 snippets)
            selected_candidates = []
            uncovered_words = set(search_words)

            while len(selected_candidates) < 10 and uncovered_words:
                best_cand = None
                best_cover_count = 0
                for cand in candidates:
                    if cand in selected_candidates:
                        continue
                    cover_count = len(cand["matched_words"] & uncovered_words)
                    if cover_count > best_cover_count:
                        best_cover_count = cover_count
                        best_cand = cand
                    elif cover_count == best_cover_count and best_cover_count > 0:
                        if cand["index"] < best_cand["index"]:
                            best_cand = cand

                if best_cand is None or best_cover_count == 0:
                    break

                selected_candidates.append(best_cand)
                uncovered_words -= best_cand["matched_words"]

            # Fill remaining slots up to 10 chronologically
            if len(selected_candidates) < 10:
                for cand in candidates:
                    if len(selected_candidates) >= 10:
                        break
                    if cand not in selected_candidates:
                        selected_candidates.append(cand)

            selected_candidates.sort(key=lambda x: x["index"])

            for cand in selected_candidates:
                line_hl = highlight_text(cand["line"], search_words, CLR_BOLD + CLR_YELLOW, CLR_WHITE)
                matched_snippets.append(
                    f"    {CLR_DIM}[Step {cand['step_idx']} | {cand['desc']}]{CLR_RESET} {CLR_WHITE}{line_hl}{CLR_RESET}"
                )

    if not is_matched:
        return None

    # Inspect affected files on filesystem
    detailed_files = []
    for f_path, f_action in sorted(affected_files.items()):
        # Handle relative or absolute paths
        abs_p = f_path if os.path.isabs(f_path) else os.path.join(session_cwd, f_path)
        exists = os.path.exists(abs_p)
        f_size = 0
        f_mtime = "N/A"
        is_text = False
        content = None
        if exists:
            try:
                st = os.stat(abs_p)
                f_size = st.st_size
                f_mtime = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')
                is_text = is_text_file(abs_p)
                if is_text:
                    with open(abs_p, "r", encoding="utf-8", errors="replace") as tf:
                        content = tf.read()
            except Exception:
                pass

        detailed_files.append({
            "path": f_path,
            "abs_path": abs_p,
            "action": f_action,
            "exists": exists,
            "size": f_size,
            "mtime": f_mtime,
            "is_text": is_text,
            "content": content
        })

    # Check for adjacent HTML export
    html_export_path = None
    parent_dir = os.path.dirname(file_path)
    base_stem = os.path.splitext(os.path.basename(file_path))[0]
    expected_html = os.path.join(parent_dir, f"pi-session-{base_stem}.html")
    if os.path.exists(expected_html):
        html_export_path = expected_html
    else:
        # Check by UUID in html filenames
        for hf in glob.glob(os.path.join(parent_dir, f"*{session_id}*.html")):
            html_export_path = hf
            break

    return {
        "id": session_id,
        "name": session_name,
        "path": file_path,
        "cwd": session_cwd,
        "version": session_version,
        "parent_session": parent_session,
        "mtime": mtime,
        "mtime_str": mtime_str,
        "duration": duration_str,
        "total_steps": total_steps,
        "active_steps": active_steps,
        "has_branches": has_branches,
        "user_prompts": user_prompts,
        "models": sorted(list(models_used)),
        "thinking_level": thinking_levels[-1] if thinking_levels else None,
        "tool_counts": tool_counts,
        "tool_calls_total": sum(tool_counts.values()),
        "thoughts": thought_count,
        "token_usage": token_usage,
        "cost": cost_total,
        "affected_files": detailed_files,
        "html_export": html_export_path,
        "matched_snippets": matched_snippets,
        "total_matches": total_matches
    }

def find_all_session_files(sessions_root: Path, target_cwd: str = None, show_all: bool = False) -> list:
    """Discovers all .jsonl session files in the sessions root directory."""
    debug_log(f"Scanning sessions root: {sessions_root}")
    if not sessions_root.is_dir():
        debug_log(f"Sessions root does not exist: {sessions_root}")
        return []

    session_files = []
    # If not show_all and target_cwd is specified, try the fast encoded directory first
    if not show_all and target_cwd:
        encoded_dir_name = encode_cwd(target_cwd)
        encoded_dir = sessions_root / encoded_dir_name
        if encoded_dir.is_dir():
            for p in sorted(encoded_dir.glob("*.jsonl"), key=os.path.getmtime, reverse=True):
                session_files.append(str(p))
            debug_log(f"Found {len(session_files)} session files in encoded dir: {encoded_dir}")
            return session_files

    # Global scan across all encoded subdirectories
    for entry in sessions_root.iterdir():
        if entry.is_dir() and entry.name.startswith("--") and entry.name.endswith("--"):
            for p in entry.glob("*.jsonl"):
                session_files.append(str(p))

    debug_log(f"Discovered {len(session_files)} total session files across all directories.")
    return session_files

def list_sessions(
    show_all: bool = False,
    num_first: int = 1,
    num_last: int = 1,
    verbosity: int = 0,
    search_words: list = None,
    use_turn_matching: bool = False,
    search_all: bool = False,
    session_dir: str = None,
    all_entries: bool = False
) -> None:
    """Catalogs and displays formatted summaries of Pi agent sessions."""
    sessions_root = get_sessions_root(session_dir)
    cwd = os.path.realpath(os.getcwd())

    if show_all:
        print(f"[*] Analyzing Pi conversation session(s) across all workspaces ({sessions_root})...")
    else:
        print(f"[*] Analyzing Pi conversation session(s) for current directory ({cwd})...")
        print(f"[*] (Use '-a' or '--all' to display all workspaces)")

    if search_words:
        mode_str = "Turn-restricted" if use_turn_matching else "Session-wide"
        words_fmt = ", ".join([f"'{w}'" for w in search_words])
        print(f"[*] Filtered by --grep query: {words_fmt} (Matching Mode: {mode_str})")

    file_paths = find_all_session_files(sessions_root, target_cwd=cwd, show_all=show_all)
    if not file_paths:
        print(f"{CLR_YELLOW}[!] No Pi session logs found in {sessions_root}.{CLR_RESET}")
        return

    sessions = []
    for fp in file_paths:
        s_data = process_session_file(
            fp,
            target_cwd=cwd,
            show_all=show_all,
            search_words=search_words,
            use_turn_matching=use_turn_matching,
            search_all=search_all,
            active_branch_only=not all_entries
        )
        if s_data:
            sessions.append(s_data)

    sessions.sort(key=lambda x: x["mtime"], reverse=True)

    if not sessions:
        if search_words:
            print(f"{CLR_YELLOW}[!] No sessions matching grep query.{CLR_RESET}")
        else:
            print(f"{CLR_YELLOW}[!] No matching conversation logs found for current directory.{CLR_RESET}")
            print(f"[*] Use {CLR_BOLD}pi-explore -a{CLR_RESET} to list all sessions.")
        return

    for idx, s in enumerate(sessions, 1):
        name_str = f" {CLR_DIM}(Name: {s['name']}){CLR_RESET}" if s.get("name") else ""
        print(f"\n{CLR_BOLD}{CLR_BLUE}================================================================================{CLR_RESET}")
        print(f"{CLR_BOLD}{CLR_GREEN}[#{idx}] Session ID: {s['id']}{CLR_RESET}{name_str}")
        print(f"{CLR_BOLD}{CLR_BLUE}================================================================================{CLR_RESET}")
        print(f"  {CLR_BOLD}Session File:     {CLR_RESET} {CLR_DIM}{s['path']}{CLR_RESET}")
        print(f"  {CLR_BOLD}Project Directory:{CLR_RESET} {CLR_CYAN}{s['cwd']}{CLR_RESET}")

        if s.get("models"):
            models_str = ", ".join(s["models"])
            tl_str = f" (Thinking: {s['thinking_level']})" if s.get("thinking_level") else ""
            print(f"  {CLR_BOLD}Model:            {CLR_RESET} {CLR_YELLOW}{models_str}{tl_str}{CLR_RESET}")

        branch_note = f" (Active trajectory: {s['active_steps']}/{s['total_steps']})" if s.get("has_branches") else ""
        print(f"  {CLR_BOLD}Active Duration:  {CLR_RESET} {s['duration']} (Last Activity: {s['mtime_str']})")
        print(f"  {CLR_BOLD}Statistics:       {CLR_RESET} {s['total_steps']} entries{branch_note} | {s['tool_calls_total']} tool calls | {s['thoughts']} reasoning cycles")

        # Token & Cost Telemetry
        tu = s.get("token_usage", {})
        if tu.get("totalTokens", 0) > 0:
            tokens_fmt = (
                f"{format_tokens(tu['totalTokens'])} tokens "
                f"(In: {format_tokens(tu['input'])}, Out: {format_tokens(tu['output'])}, Cache: {format_tokens(tu['cacheRead'])})"
            )
            cost_fmt = format_cost(s.get("cost", 0.0))
            print(f"  {CLR_BOLD}Tokens / Cost:    {CLR_RESET} {CLR_MAGENTA}{tokens_fmt} | Cost: {cost_fmt}{CLR_RESET}")

        # Tool Breakdown
        if (verbosity >= 1 or search_words) and s.get("tool_counts"):
            breakdown_str = ", ".join([f"{name}: {count}" for name, count in sorted(s["tool_counts"].items())])
            print(f"  {CLR_BOLD}Tool Breakdown:   {CLR_RESET} {CLR_CYAN}{breakdown_str}{CLR_RESET}")

        # Affected Workspace Files
        affected = s.get("affected_files", [])
        if affected:
            if verbosity == 0:
                short_paths = [f["path"] for f in affected]
                f_str = ", ".join(short_paths)
                f_str_hl = highlight_text(f_str, search_words, CLR_BOLD + CLR_YELLOW, CLR_CYAN)
                print(f"  {CLR_BOLD}Affected Files:   {CLR_RESET} {CLR_CYAN}{f_str_hl}{CLR_RESET}")
            else:
                print(f"  {CLR_BOLD}Affected Files ({len(affected)}):{CLR_RESET}")
                for f in affected:
                    p_hl = highlight_text(f["path"], search_words, CLR_BOLD + CLR_YELLOW, CLR_CYAN)
                    status_note = f"({f['action']}, {format_size(f['size'])}, {f['mtime']})" if f["exists"] else f"({f['action']}, deleted/not on disk)"
                    print(f"    {CLR_CYAN}- {p_hl} {CLR_DIM}{status_note}{CLR_RESET}")

        # First and Last Prompts
        prompts = s.get("user_prompts", [])
        if num_first == 1:
            val = f"\"{prompts[0]}\"" if prompts else "\"N/A\""
            val_hl = highlight_text(val, search_words, CLR_BOLD + CLR_YELLOW, CLR_WHITE)
            print(f"  {CLR_BOLD}First Prompt:     {CLR_RESET} {CLR_WHITE}{val_hl}{CLR_RESET}")
        elif num_first > 1:
            first_sub = prompts[:num_first]
            if first_sub:
                print(f"  {CLR_BOLD}First Prompts ({len(first_sub)}):{CLR_RESET}")
                for p in first_sub:
                    p_hl = highlight_text(p, search_words, CLR_BOLD + CLR_YELLOW, CLR_WHITE)
                    print(f"    {CLR_DIM}-{CLR_RESET} {CLR_WHITE}\"{p_hl}\"{CLR_RESET}")
            else:
                print(f"  {CLR_BOLD}First Prompts:    {CLR_RESET} {CLR_WHITE}\"N/A\"{CLR_RESET}")

        if num_last == 1:
            val = f"\"{prompts[-1]}\"" if prompts else "\"N/A\""
            val_hl = highlight_text(val, search_words, CLR_BOLD + CLR_YELLOW, CLR_WHITE)
            print(f"  {CLR_BOLD}Last Prompt:      {CLR_RESET} {CLR_WHITE}{val_hl}{CLR_RESET}")
        elif num_last > 1:
            last_sub = prompts[-num_last:]
            if last_sub:
                print(f"  {CLR_BOLD}Last Prompts ({len(last_sub)}):{CLR_RESET}")
                for p in last_sub:
                    p_hl = highlight_text(p, search_words, CLR_BOLD + CLR_YELLOW, CLR_WHITE)
                    print(f"    {CLR_DIM}-{CLR_RESET} {CLR_WHITE}\"{p_hl}\"{CLR_RESET}")
            else:
                print(f"  {CLR_BOLD}Last Prompts:     {CLR_RESET} {CLR_WHITE}\"N/A\"{CLR_RESET}")

        # Resume Command
        s_id = s["id"]
        ws = s["cwd"]
        if ws:
            resume_cmd = f"cd {ws} && pi --session {s_id}"
        else:
            resume_cmd = f"pi --session {s_id}"
        print(f"  {CLR_BOLD}{CLR_YELLOW}Resume CLI:       {CLR_RESET} {CLR_BOLD}{resume_cmd}{CLR_RESET}")

        if s.get("html_export"):
            print(f"  {CLR_BOLD}HTML Export:      {CLR_RESET} {CLR_DIM}file://{s['html_export']}{CLR_RESET}")

        # Matched Snippets
        snippets = s.get("matched_snippets", [])
        total_matches = s.get("total_matches", len(snippets))
        if snippets:
            print(f"  {CLR_BOLD}Matched Snippets ({total_matches}):{CLR_RESET}")
            for snip in snippets:
                print(snip)
            if total_matches > len(snippets):
                print(f"    {CLR_DIM}... and {total_matches - len(snippets)} more matching lines ...{CLR_RESET}")

        # Level 2+ Verbosity: Print full contents of modified text files
        if verbosity >= 2 and affected:
            print(f"\n  {CLR_BOLD}Affected Files Content:{CLR_RESET}")
            for f in affected:
                if f["is_text"] and f["content"] is not None:
                    p_hl = highlight_text(f["path"], search_words, CLR_BOLD + CLR_YELLOW, CLR_CYAN)
                    print(f"    {CLR_CYAN}[File: {p_hl}]{CLR_RESET}")
                    print(f"    {CLR_DIM}{'-'*80}{CLR_RESET}")
                    for line in f["content"].splitlines():
                        l_hl = highlight_text(line, search_words, CLR_BOLD + CLR_YELLOW, CLR_WHITE)
                        print(f"    {CLR_WHITE}{l_hl}{CLR_RESET}")
                    print(f"    {CLR_DIM}{'-'*80}{CLR_RESET}")

    print("\n" + "="*80 + "\n")

def render_transcript(
    file_path: str,
    use_color: bool = True,
    show_thoughts: bool = True,
    show_tools: bool = True,
    all_entries: bool = False
) -> None:
    """Streams a color-coded, human-readable conversation transcript to stdout."""
    if not use_color:
        global CLR_RESET, CLR_BOLD, CLR_DIM, CLR_RED, CLR_GREEN, CLR_YELLOW, CLR_BLUE, CLR_MAGENTA, CLR_CYAN, CLR_WHITE
        CLR_RESET = CLR_BOLD = CLR_DIM = CLR_RED = CLR_GREEN = CLR_YELLOW = CLR_BLUE = CLR_MAGENTA = CLR_CYAN = CLR_WHITE = ""

    entries = load_session_entries(file_path)
    if not entries:
        print(f"{CLR_RED}[-] No entries found in {file_path}.{CLR_RESET}")
        return

    active_path = build_active_path(entries)
    has_branches = len(active_path) < len(entries)
    entries_to_render = entries if all_entries else active_path

    header = entries[0]
    session_id = header.get("id", "unknown")
    cwd = header.get("cwd", "unknown")

    print(f"{CLR_BOLD}{CLR_CYAN}[*] Streaming Pi Session Transcript: {file_path}{CLR_RESET}")
    print(f"{CLR_DIM}Session ID: {session_id} | Workspace: {cwd}{CLR_RESET}")
    if has_branches and not all_entries:
        print(f"{CLR_DIM}(Rendering active leaf branch: {len(active_path)}/{len(entries)} entries. Use --all-entries to show abandoned branches.){CLR_RESET}\n")
    else:
        print("")

    tool_calls_map = {}
    total_tokens = 0
    total_cost = 0.0

    for idx, entry in enumerate(entries_to_render, 1):
        e_type = entry.get("type")
        ts_raw = entry.get("timestamp", "N/A")
        ts_clean = ts_raw.replace("Z", "").replace("T", " ")[:19]

        if e_type == "session":
            continue

        elif e_type == "model_change":
            prov = entry.get("provider", "unknown")
            mid = entry.get("modelId", "unknown")
            print(f"{CLR_DIM}{CLR_MAGENTA}🔄 MODEL SWITCH: {prov}/{mid} | Timestamp: {ts_clean}{CLR_RESET}\n")

        elif e_type == "thinking_level_change":
            tl = entry.get("thinkingLevel", "unknown")
            print(f"{CLR_DIM}{CLR_MAGENTA}🧠 THINKING LEVEL CHANGED: {tl} | Timestamp: {ts_clean}{CLR_RESET}\n")

        elif e_type == "compaction":
            print(f"{CLR_BOLD}{CLR_BLUE}================================================================================{CLR_RESET}")
            print(f"{CLR_BOLD}{CLR_BLUE}📋 SESSION COMPACTION / CHECKPOINT | Timestamp: {ts_clean}{CLR_RESET}")
            print(f"{CLR_BOLD}{CLR_BLUE}================================================================================{CLR_RESET}")
            print(f"{entry.get('summary', '')}\n")

        elif e_type == "branch_summary":
            print(f"{CLR_BOLD}{CLR_MAGENTA}================================================================================{CLR_RESET}")
            print(f"{CLR_BOLD}{CLR_MAGENTA}🌿 BRANCH SUMMARY | Timestamp: {ts_clean}{CLR_RESET}")
            print(f"{CLR_BOLD}{CLR_MAGENTA}================================================================================{CLR_RESET}")
            print(f"{entry.get('summary', '')}\n")

        elif e_type == "custom_message":
            c_type = entry.get("customType", "custom")
            content = entry.get("content", "")
            print(f"{CLR_DIM}{CLR_CYAN}ℹ️ [{c_type.upper()}] {content} | Timestamp: {ts_clean}{CLR_RESET}\n")

        elif e_type == "message":
            msg = entry.get("message", {})
            role = msg.get("role")
            content = msg.get("content", [])

            if role == "user":
                user_text = ""
                if isinstance(content, list):
                    text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    user_text = "\n".join(text_parts)
                elif isinstance(content, str):
                    user_text = content

                print(f"{CLR_BOLD}{CLR_BLUE}================================================================================{CLR_RESET}")
                print(f"{CLR_BOLD}{CLR_BLUE}👤 USER INPUT | Timestamp: {ts_clean}{CLR_RESET}")
                print(f"{CLR_BOLD}{CLR_BLUE}================================================================================{CLR_RESET}")
                print(f"{user_text.strip()}\n")

            elif role == "assistant":
                m_model = msg.get("model", "")
                m_prov = msg.get("provider", "")
                model_tag = f" | Model: {m_prov}/{m_model}" if m_model else ""

                usage = msg.get("usage", {})
                t_tokens = usage.get("totalTokens", 0)
                cost = usage.get("cost", {}).get("total", 0.0)
                total_tokens += t_tokens
                total_cost += cost

                telemetry_tag = f" | {format_tokens(t_tokens)} tokens" if t_tokens > 0 else ""

                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        b_type = b.get("type")

                        if b_type == "thinking" and show_thoughts:
                            th_text = b.get("thinking", "").strip()
                            if th_text:
                                print(f"{CLR_DIM}{CLR_YELLOW}--------------------------------------------------------------------------------{CLR_RESET}")
                                print(f"{CLR_DIM}{CLR_YELLOW}🤖 ASSISTANT INTERNAL THOUGHTS{CLR_RESET}")
                                print(f"{CLR_DIM}{CLR_YELLOW}--------------------------------------------------------------------------------{CLR_RESET}")
                                print(f"{CLR_DIM}{CLR_YELLOW}{th_text}{CLR_RESET}\n")

                        elif b_type == "text":
                            resp_text = b.get("text", "").strip()
                            if resp_text:
                                print(f"{CLR_BOLD}{CLR_GREEN}--------------------------------------------------------------------------------{CLR_RESET}")
                                print(f"{CLR_BOLD}{CLR_GREEN}🤖 ASSISTANT RESPONSE{model_tag}{telemetry_tag} | Timestamp: {ts_clean}{CLR_RESET}")
                                print(f"{CLR_BOLD}{CLR_GREEN}--------------------------------------------------------------------------------{CLR_RESET}")
                                print(f"{resp_text}\n")

                        elif b_type == "toolCall":
                            tc_id = b.get("id")
                            tc_name = b.get("name", "unknown")
                            tc_args = b.get("arguments", {})
                            if tc_id:
                                tool_calls_map[tc_id] = tc_name

                            if show_tools:
                                print(f"{CLR_BOLD}{CLR_MAGENTA}🛠️ TOOL CALL REQUESTED | Tool: {tc_name}{CLR_RESET}")
                                print(f"{CLR_DIM}{json.dumps(tc_args, indent=2)}{CLR_RESET}\n")

                if msg.get("stopReason") == "error":
                    err_msg = msg.get("errorMessage", "Unknown assistant error")
                    print(f"{CLR_BOLD}{CLR_RED}⚠️ ASSISTANT ERROR | {err_msg}{CLR_RESET}\n")

            elif role == "toolResult":
                tc_id = msg.get("toolCallId")
                tc_name = msg.get("toolName") or tool_calls_map.get(tc_id, "tool")
                dur_ms = msg.get("durationMs")
                dur_str = f" | Duration: {dur_ms}ms" if dur_ms is not None else ""
                is_err = msg.get("isError", False)
                status_color = CLR_RED if is_err else CLR_CYAN

                res_text = ""
                if isinstance(content, list):
                    text_parts = []
                    for b in content:
                        if isinstance(b, dict):
                            if b.get("type") == "text":
                                text_parts.append(b.get("text", ""))
                            elif b.get("type") == "image":
                                text_parts.append("[image data]")
                    res_text = "\n".join(text_parts)
                elif isinstance(content, str):
                    res_text = content

                if show_tools:
                    print(f"{CLR_BOLD}{status_color}⚙️ TOOL RESULT | {tc_name}{dur_str} | Timestamp: {ts_clean}{CLR_RESET}")
                    print(f"{status_color}{res_text.strip()}{CLR_RESET}\n")

    print(f"{CLR_BOLD}{CLR_BLUE}================================================================================{CLR_RESET}")
    print(f"{CLR_BOLD}{CLR_BLUE}🏁 END OF TRANSCRIPT{CLR_RESET} {CLR_DIM}(Total Tokens: {format_tokens(total_tokens)} | Total Cost: {format_cost(total_cost)}){CLR_RESET}")
    print(f"{CLR_BOLD}{CLR_BLUE}================================================================================{CLR_RESET}\n")

def export_session_to_html(target_file: str, output_path: str = None) -> None:
    """Exports a session to HTML format using Pi CLI or reports status."""
    pi_bin = shutil.which("pi")
    if not pi_bin:
        print(f"{CLR_RED}[-] Error: 'pi' command not found in PATH.{CLR_RESET}")
        sys.exit(1)

    cmd = [pi_bin, "--export", target_file]
    if output_path:
        cmd.append(output_path)

    print(f"[*] Running: {' '.join(cmd)}")
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print(f"{CLR_GREEN}[+] Export completed successfully.{CLR_RESET}")
    else:
        print(f"{CLR_RED}[-] Export failed with exit code {res.returncode}.{CLR_RESET}")

def resolve_target_session(target: str, session_dir: str = None) -> str:
    """Resolves a target argument to a concrete session JSONL file path."""
    # 1. Direct file path check
    if os.path.isfile(target):
        return os.path.realpath(target)

    sessions_root = get_sessions_root(session_dir)
    all_files = find_all_session_files(sessions_root, show_all=True)

    # 2. Exact UUID match
    for f in all_files:
        bname = os.path.basename(f)
        if target in bname:
            # Check header ID
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    first_line = fp.readline()
                    if first_line:
                        d = json.loads(first_line)
                        if d.get("id") == target:
                            return f
            except Exception:
                pass

    # 3. Prefix UUID match
    for f in all_files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                first_line = fp.readline()
                if first_line:
                    d = json.loads(first_line)
                    if d.get("id", "").startswith(target):
                        return f
        except Exception:
            pass

    # 4. Filename substring match
    for f in all_files:
        if target in os.path.basename(f):
            return f

    return None

def print_help() -> None:
    """Prints the CLI help manual and usage instructions."""
    print(f"{CLR_BOLD}Explore Pi Conversation Transcripts & Manage Sessions{CLR_RESET}")
    print("Usage:")
    print("  pi-explore                       # List sessions for current directory")
    print("  pi-explore -a, --all             # List sessions across all workspaces")
    print("  pi-explore <session_id>          # Print full conversation log for UUID / partial ID")
    print("  pi-explore <path_to_jsonl_file>  # Print specific JSONL file")
    print("\nOptions:")
    print("  -a, --all               List all sessions globally across all directories")
    print("  -d, --debug             Enable detailed stderr diagnostic telemetry logging")
    print("  -v, --verbose           Increase verbosity (-v: tool breakdown and file stats, -vv: file contents)")
    print("  --grep <words>          Find sessions containing all specified words (case-insensitive AND)")
    print("  --turn                  Restrict --grep search to match all words within a single turn")
    print("  --search-all            Search all fields (thoughts, tool outputs) instead of just dialogue")
    print("  --first <N>             Specify number of first prompts to show (default: 1)")
    print("  --last <M>              Specify number of last prompts to show (default: 1)")
    print("  --no-color              Disable ANSI terminal color sequences")
    print("  --no-thoughts           Exclude assistant internal reasoning blocks in transcript")
    print("  --no-tools              Exclude tool calls and execution outputs in transcript")
    print("  --all-entries           Show all entries including abandoned branch forks (default: active branch)")
    print("  --session-dir <path>    Specify custom session storage directory (default: ~/.pi/agent/sessions)")
    print("  --export [output.html]  Export target session to HTML using 'pi --export'")
    print("  -h, --help              Show this help manual")
    print("\nExamples:")
    print("  pi-explore")
    print("  pi-explore -a --grep refactor auth")
    print("  pi-explore 019db197 | less -R")
    print("  pi-explore --export output.html 019db197")
    sys.exit(0)

def main() -> None:
    global DEBUG_MODE
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print_help()

    use_color = "--no-color" not in args
    show_thoughts = "--no-thoughts" not in args
    show_tools = "--no-tools" not in args
    show_all = "-a" in args or "--all" in args
    all_entries = "--all-entries" in args
    DEBUG_MODE = "-d" in args or "--debug" in args

    # Parse verbosity
    verbosity = 0
    for arg in args:
        if arg == "-v":
            verbosity = max(verbosity, 1)
        elif arg == "-vv":
            verbosity = max(verbosity, 2)
        elif arg == "-vvv":
            verbosity = max(verbosity, 3)
        elif arg.startswith("-") and not arg.startswith("--"):
            clean_arg = arg[1:]
            if "v" in clean_arg:
                verbosity = max(verbosity, clean_arg.count("v"))

    # Parse custom session dir
    session_dir = None
    if "--session-dir" in args:
        try:
            s_idx = args.index("--session-dir")
            if s_idx + 1 < len(args):
                session_dir = args[s_idx + 1]
        except Exception:
            pass

    # Parse --first and --last
    num_first = 1
    num_last = 1
    if "--first" in args:
        try:
            idx = args.index("--first")
            if idx + 1 < len(args):
                num_first = int(args[idx + 1])
        except Exception:
            pass

    if "--last" in args:
        try:
            idx = args.index("--last")
            if idx + 1 < len(args):
                num_last = int(args[idx + 1])
        except Exception:
            pass

    # Parse grep options
    search_words = []
    use_turn_matching = "--turn" in args
    search_all = "--search-all" in args
    if "--grep" in args:
        idx = args.index("--grep")
        grep_terms = []
        for arg in args[idx + 1:]:
            if arg.startswith("-"):
                break
            grep_terms.append(arg)
        for term in grep_terms:
            search_words.extend([w.strip() for w in term.split() if w.strip()])

        debug_log(f"Grep search enabled. Words: {search_words}, Turn: {use_turn_matching}")
        list_sessions(
            show_all=show_all,
            num_first=num_first,
            num_last=num_last,
            verbosity=verbosity,
            search_words=search_words,
            use_turn_matching=use_turn_matching,
            search_all=search_all,
            session_dir=session_dir,
            all_entries=all_entries
        )
        sys.exit(0)

    # Filter out positional arguments
    pos_args = []
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg in ["--first", "--last", "--session-dir"]:
            skip_next = True
            continue
        if arg == "--export":
            # export can take an optional filename or positional target
            continue
        if arg.startswith("-"):
            continue
        pos_args.append(arg)

    # Export flag handling
    if "--export" in args:
        exp_idx = args.index("--export")
        exp_out = None
        if exp_idx + 1 < len(args) and not args[exp_idx + 1].startswith("-"):
            exp_out = args[exp_idx + 1]
            if exp_out in pos_args:
                pos_args.remove(exp_out)

        target = pos_args[0] if pos_args else None
        if not target:
            print(f"{CLR_RED}[-] Error: Please specify a session ID or file path to export.{CLR_RESET}")
            sys.exit(1)

        resolved_path = resolve_target_session(target, session_dir)
        if not resolved_path:
            print(f"{CLR_RED}[-] Error: Could not resolve session '{target}'.{CLR_RESET}")
            sys.exit(1)

        export_session_to_html(resolved_path, exp_out)
        sys.exit(0)

    # If no target specified, list sessions
    if not pos_args:
        debug_log("No positional target specified. Listing sessions.")
        list_sessions(
            show_all=show_all,
            num_first=num_first,
            num_last=num_last,
            verbosity=verbosity,
            session_dir=session_dir,
            all_entries=all_entries
        )
        sys.exit(0)

    # Otherwise render conversation transcript
    target = pos_args[0]
    debug_log(f"Resolving transcript target: '{target}'")
    resolved_path = resolve_target_session(target, session_dir)

    if resolved_path and os.path.exists(resolved_path):
        render_transcript(
            resolved_path,
            use_color=use_color,
            show_thoughts=show_thoughts,
            show_tools=show_tools,
            all_entries=all_entries
        )
        sys.exit(0)

    print(f"{CLR_RED}[-] Error: Could not resolve '{target}' to an existing session file or UUID.{CLR_RESET}")
    print("Run without arguments to list all active sessions.")
    sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except Exception:
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
