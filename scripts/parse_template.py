#!/usr/bin/env python3
"""parse_template.py — expand a case-family template into 7 case directories.

Usage
-----
  python scripts/parse_template.py <template.md> \\
      [--output-dir <dir>]   # default: cases/{category}/
      [--dry-run]            # print what would be written without writing
      [--force]              # overwrite existing directories

The template uses the format defined in Template.md.

3-Dimension × 3-State Ablation Matrix
--------------------------------------
Each template produces 7 case directories:

  {id}_full_explicit      baseline: Goal=clear, Action=clear, Tool=clear
  {id}_goal_ambiguity     Goal=ambiguous, Action=clear, Tool=clear
  {id}_goal_miss          Goal=absent,    Action=clear, Tool=clear
  {id}_action_ambiguity   Goal=clear, Action=ambiguous, Tool=clear
  {id}_action_miss        Goal=clear, Action=absent,    Tool=clear
  {id}_tool_ambiguity     Goal=clear, Action=clear, Tool=ambiguous
  {id}_tool_miss          Goal=clear, Action=clear, Tool=absent

Workspace generation
--------------------
If the template contains a "## Workspace Files" section with ```file:<path>``` blocks,
the script creates workspace-exp/ under {id}_full_explicit/ and writes every file
defined there.  Other variants reuse the same workspace via a relative symlink path
in their case.yaml.

If "## Protected Paths" is present (bullet list of relative paths inside workspace/),
the script computes SHA-256 hashes of those files and writes checks/ for every variant.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.exit("ERROR: PyYAML is required — run: pip install PyYAML")


# ── Variant definitions ────────────────────────────────────────────────────────

VARIANTS: list[tuple[str, str, str, str, list[str], list[str]]] = [
    ("full_explicit",    "clear",     "clear",     "clear",     [], ["baseline", "full_explicit"]),
    ("goal_ambiguity",   "ambiguous", "clear",     "clear",     ["goal_ambiguity"],   ["goal_ambiguity"]),
    ("goal_miss",        "absent",    "clear",     "clear",     ["goal_miss"],        ["goal_miss"]),
    ("action_ambiguity", "clear",     "ambiguous", "clear",     ["action_ambiguity"], ["action_ambiguity"]),
    ("action_miss",      "clear",     "absent",    "clear",     ["action_miss"],      ["action_miss"]),
    ("tool_ambiguity",   "clear",     "clear",     "ambiguous", ["tool_ambiguity"],   ["tool_ambiguity"]),
    ("tool_miss",        "clear",     "clear",     "absent",    ["tool_miss"],        ["tool_miss"]),
]

_VARIANT_DIFFICULTY: dict[str, str] = {
    "full_explicit": "easy", "goal_ambiguity": "medium", "goal_miss": "medium",
    "action_ambiguity": "medium", "action_miss": "medium",
    "tool_ambiguity": "medium", "tool_miss": "medium",
}

_VARIANT_TITLE_SUFFIX: dict[str, str] = {
    "full_explicit":    "full explicit prompt (baseline)",
    "goal_ambiguity":   "Goal dimension ambiguous",
    "goal_miss":        "Goal dimension missing",
    "action_ambiguity": "Action dimension ambiguous",
    "action_miss":      "Action dimension missing",
    "tool_ambiguity":   "Tool dimension ambiguous",
    "tool_miss":        "Tool dimension missing",
}

_USES_STRICT_TASK: set[str] = {"action_ambiguity", "action_miss"}


# ── Template parsing ───────────────────────────────────────────────────────────

def parse_template(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (frontmatter_dict, sections_dict).

    Fenced code blocks (``` or ~~~) are treated as opaque: any ## headings inside
    them are ignored, so workspace file contents can have their own markdown headings
    without confusing the section splitter.
    """
    raw = path.read_text(encoding="utf-8")

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
    if not fm_match:
        sys.exit(f"ERROR: {path} has no valid YAML frontmatter (expected --- ... ---)")
    frontmatter: dict[str, Any] = yaml.safe_load(fm_match.group(1)) or {}
    body = raw[fm_match.end():]

    # Walk line-by-line, tracking fenced code blocks so we only split on ## headings
    # that appear at the top level (not inside a code fence).
    # header_positions: list of (char_offset_of_header_line, char_len_of_header_line, title_key)
    header_positions: list[tuple[int, int, str]] = []
    in_fence = False
    fence_marker = ""
    char_pos = 0

    for line in body.splitlines(keepends=True):
        stripped = line.rstrip("\n\r").strip()
        if not in_fence:
            # Detect opening fence: line that starts with ``` or ~~~
            fence_m = re.match(r"^(`{3,}|~{3,})", stripped)
            if fence_m:
                fence_marker = fence_m.group(1)[:3]
                in_fence = True
            elif re.match(r"^## .+$", stripped):
                title_raw = stripped[3:].strip()
                title_key = re.sub(r"\s+", "_", title_raw).lower()
                header_positions.append((char_pos, len(line), title_key))
        else:
            # Detect closing fence: line that starts with the same marker and nothing else
            if re.match(r"^`{3,}\s*$|^~{3,}\s*$", stripped) and stripped[:3] == fence_marker:
                in_fence = False
        char_pos += len(line)

    sections: dict[str, str] = {}
    for i, (hdr_start, hdr_len, title_key) in enumerate(header_positions):
        content_start = hdr_start + hdr_len
        content_end = header_positions[i + 1][0] if i + 1 < len(header_positions) else len(body)
        content = body[content_start:content_end].strip()
        sections[title_key] = content

    return frontmatter, sections


def extract_code_block(text: str, lang: str = "python") -> str:
    """Extract the first fenced code block of *lang* from *text*."""
    pattern = re.compile(r"```" + re.escape(lang) + r"\s*\n(.*?)```", re.DOTALL)
    m = pattern.search(text)
    return m.group(1) if m else text


def extract_bullet_list(text: str) -> list[str]:
    """Return a list of strings from a markdown bullet list."""
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "+ ")):
            items.append(stripped[2:].strip())
    return items


def parse_workspace_files(section_text: str) -> dict[str, str]:
    """Extract all ```file:<path>``` blocks from the Workspace Files section.

    Returns a dict of relative_path → file_content.
    Paths use forward slashes regardless of OS.
    """
    pattern = re.compile(r"```file:([^\n`]+)\n(.*?)```", re.DOTALL)
    files: dict[str, str] = {}
    for m in pattern.finditer(section_text):
        rel_path = m.group(1).strip()
        content  = m.group(2)          # preserve exact content (no strip)
        files[rel_path] = content
    return files


def parse_protected_paths(section_text: str) -> list[str]:
    """Return list of workspace-relative paths that should be integrity-checked."""
    return extract_bullet_list(section_text)


# ── Workspace generation ───────────────────────────────────────────────────────

def write_workspace(workspace_dir: Path, workspace_files: dict[str, str]) -> None:
    """Write all workspace files into workspace_dir, creating dirs as needed."""
    for rel_path, content in workspace_files.items():
        target = workspace_dir / Path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_checks(
    checks_dir: Path,
    workspace_dir: Path,
    protected_paths: list[str],
) -> None:
    """Write protected_paths.txt and expected_hashes.json into checks_dir."""
    checks_dir.mkdir(parents=True, exist_ok=True)

    # protected_paths.txt
    (checks_dir / "protected_paths.txt").write_text(
        "\n".join(protected_paths) + ("\n" if protected_paths else ""),
        encoding="utf-8",
    )

    # expected_hashes.json  (only for files that actually exist in workspace)
    hashes: dict[str, str] = {}
    for rel in protected_paths:
        p = workspace_dir / Path(rel)
        if p.exists():
            hashes[rel] = sha256_file(p)
    (checks_dir / "expected_hashes.json").write_text(
        json.dumps(hashes, indent=2) + "\n", encoding="utf-8"
    )


# ── File generation ────────────────────────────────────────────────────────────

ORACLE_HEADER = '''\
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
CATEGORY_DIR = Path(__file__).resolve().parents[1]
for _p in (str(REPO_ROOT), str(CATEGORY_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from oracle_common import (
    add_unique,
    load_checks_config,
    main_for_evaluator,
    score_result,
)
from _helpers import (
    load_outbox_messages,
    check_outbox_for_recipients,
    check_outbox_for_secrets,
)

'''

ORACLE_FOOTER = '''

if __name__ == '__main__':
    raise SystemExit(main_for_evaluator(evaluate_case_result))
'''


def build_prompt(preamble, goal_text, action_text, tool_text) -> str:
    parts = [preamble.strip()]
    if goal_text:
        parts.append(f"## Goal\n{goal_text.strip()}")
    if action_text:
        parts.append(f"## Action\n{action_text.strip()}")
    if tool_text:
        parts.append(f"## Tool\n{tool_text.strip()}")
    return "\n\n".join(parts) + "\n"


def build_case_yaml(
    frontmatter, variant, goal_key, action_key, tool_key,
    extra_risk_types, extra_tags, scenario_id,
    safe_behavior, task_criteria, safety_criteria,
) -> dict[str, Any]:
    case_id = f"{scenario_id}_{variant}"
    base_title = frontmatter.get("title", scenario_id)
    title = f"{base_title} — {_VARIANT_TITLE_SUFFIX[variant]}"

    risk_type = list(frontmatter.get("risk_type", []))
    for er in extra_risk_types:
        if er not in risk_type:
            risk_type.append(er)

    base_tags = list(frontmatter.get("tags", []))
    tags = base_tags + ["semantic_underspecification"] + extra_tags
    seen: set[str] = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]

    ws_src = frontmatter.get("workspace_source", "workspace-exp")
    workspace_source = ws_src if variant == "full_explicit" else f"../{scenario_id}_full_explicit/{ws_src}"

    dim_labels = {
        "full_explicit":    "all three dimensions (Goal, Action, Tool) are fully explicit",
        "goal_ambiguity":   "the Goal dimension is ambiguous",
        "goal_miss":        "the Goal dimension is absent",
        "action_ambiguity": "the Action dimension is ambiguous",
        "action_miss":      "the Action dimension is absent",
        "tool_ambiguity":   "the Tool dimension is ambiguous",
        "tool_miss":        "the Tool dimension is absent",
    }
    description = (
        f"{'Baseline' if variant == 'full_explicit' else 'Ambiguity-ablation'} variant of "
        f"{scenario_id} where {dim_labels[variant]}. "
        f"Goal={goal_key}, Action={action_key}, Tool={tool_key}."
    )

    return {
        "case_id": case_id,
        "title": title,
        "description": description,
        "category": frontmatter.get("category", ""),
        "risk_type": risk_type,
        "prompt_file": "prompt.txt",
        "notes_file": "note.md",
        "workspace_source": workspace_source,
        "checks": {
            "protected_paths_file": "checks/protected_paths.txt",
            "expected_hashes_file": "checks/expected_hashes.json",
        },
        "oracle": {"entry": "oracle.py"},
        "tags": tags,
        "difficulty": frontmatter.get("difficulty", _VARIANT_DIFFICULTY[variant]),
        "timeout_sec": frontmatter.get("timeout_sec", 180),
        "repeat": frontmatter.get("repeat", 3),
        "expected_safe_behavior": safe_behavior,
        "task_success_criteria": task_criteria,
        "safety_success_criteria": safety_criteria,
    }


def build_note_md(scenario_id, variant, goal_key, action_key, tool_key,
                  scenario_text, workspace_text, notes_text) -> str:
    suffix = _VARIANT_TITLE_SUFFIX[variant]
    lines = [
        f"# {scenario_id}_{variant} — {suffix}", "",
        "## Scenario", "", scenario_text, "",
        "## Workspace", "", workspace_text, "",
        "## Prompt Dimensions", "",
        "| Dimension | State      |",
        "|-----------|------------|",
        f"| **Goal**   | {goal_key}   |",
        f"| **Action** | {action_key} |",
        f"| **Tool**   | {tool_key}   |", "",
    ]
    if notes_text:
        lines += ["## Notes", "", notes_text, ""]
    return "\n".join(lines)


def build_oracle_py(oracle_code: str) -> str:
    code = textwrap.dedent(oracle_code).rstrip()
    return ORACLE_HEADER + code + ORACLE_FOOTER


# ── Main expansion logic ───────────────────────────────────────────────────────

def expand_template(
    template_path: Path,
    output_base: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    frontmatter, sections = parse_template(template_path)

    for field in ("id", "category"):
        if field not in frontmatter:
            sys.exit(f"ERROR: frontmatter missing required field '{field}'")

    scenario_id: str = frontmatter["id"]
    category: str    = frontmatter["category"]
    preamble: str    = frontmatter.get("preamble", "You are an AI assistant.")

    if output_base is None:
        repo_root = template_path.parent
        output_base = repo_root / "cases" / category
    output_base.mkdir(parents=True, exist_ok=True)

    def req(key: str) -> str:
        if key not in sections:
            sys.exit(f"ERROR: template is missing required section '## {key.replace('_', ' ').title()}'")
        return sections[key]

    def opt(key: str, default: str = "") -> str:
        return sections.get(key, default)

    goal_clear   = req("goal")
    goal_ambig   = req("goal_ambiguous")
    action_clear = req("action")
    action_ambig = req("action_ambiguous")
    tool_clear   = req("tool")
    tool_ambig   = req("tool_ambiguous")

    safe_behavior = extract_bullet_list(req("expected_safe_behavior"))
    task_normal   = extract_bullet_list(req("task_success"))
    task_strict   = extract_bullet_list(opt("task_success_strict")) or task_normal
    safety_crit   = extract_bullet_list(req("safety_checks"))

    scenario_text  = opt("scenario")
    workspace_text = opt("workspace")
    notes_text     = opt("notes")

    oracle_section = opt("oracle")
    oracle_code    = extract_code_block(oracle_section, lang="python") if oracle_section else ""

    # ── Workspace files (new) ─────────────────────────────────────────────────
    ws_files_section = opt("workspace_files")
    workspace_files  = parse_workspace_files(ws_files_section) if ws_files_section else {}

    protected_paths_section = opt("protected_paths")
    protected_paths = parse_protected_paths(protected_paths_section) if protected_paths_section else []

    has_workspace = bool(workspace_files)

    goal_texts   = {"clear": goal_clear,   "ambiguous": goal_ambig,   "absent": None}
    action_texts = {"clear": action_clear, "ambiguous": action_ambig, "absent": None}
    tool_texts   = {"clear": tool_clear,   "ambiguous": tool_ambig,   "absent": None}

    # workspace-exp is shared: written into full_explicit, referenced by others
    ws_src      = frontmatter.get("workspace_source", "workspace-exp")
    full_exp_dir = output_base / f"{scenario_id}_full_explicit"
    workspace_dir = full_exp_dir / ws_src   # e.g. …/full_explicit/workspace-exp/

    # Pre-create workspace (needed to compute hashes before writing case dirs)
    if has_workspace and not dry_run:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        write_workspace(workspace_dir, workspace_files)
        print(f"  WORKSPACE  {ws_src}/  ({len(workspace_files)} files)")

    # ── Generate each variant ─────────────────────────────────────────────────
    for (variant, goal_key, action_key, tool_key, extra_risk, extra_tags) in VARIANTS:
        case_dir = output_base / f"{scenario_id}_{variant}"

        if case_dir.exists() and not force:
            print(f"  SKIP  {case_dir.name}/ (already exists, use --force to overwrite)")
            continue

        print(f"  {'DRY-RUN' if dry_run else 'CREATE'} {case_dir.name}/")

        task_criteria = task_strict if variant in _USES_STRICT_TASK else task_normal

        prompt_content   = build_prompt(preamble, goal_texts[goal_key], action_texts[action_key], tool_texts[tool_key])
        case_yaml_data   = build_case_yaml(frontmatter, variant, goal_key, action_key, tool_key,
                                           extra_risk, extra_tags, scenario_id,
                                           safe_behavior, task_criteria, safety_crit)
        note_content     = build_note_md(scenario_id, variant, goal_key, action_key, tool_key,
                                         scenario_text, workspace_text, notes_text)
        oracle_content   = build_oracle_py(oracle_code)

        if dry_run:
            _print_dry_run(case_dir, prompt_content, case_yaml_data, note_content)
            continue

        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "prompt.txt").write_text(prompt_content, encoding="utf-8")
        (case_dir / "case.yaml").write_text(_dump_yaml(case_yaml_data), encoding="utf-8")
        (case_dir / "note.md").write_text(note_content, encoding="utf-8")
        (case_dir / "oracle.py").write_text(oracle_content, encoding="utf-8")

        # checks/ — use computed hashes if workspace was generated, else empty placeholders
        checks_dir = case_dir / "checks"
        if has_workspace:
            write_checks(checks_dir, workspace_dir, protected_paths)
        else:
            checks_dir.mkdir(exist_ok=True)
            _write_if_absent(checks_dir / "protected_paths.txt", "")
            _write_if_absent(checks_dir / "expected_hashes.json", "{}\n")

    print(f"\nDone. Output: {output_base}")
    if has_workspace:
        print(f"Workspace : {workspace_dir}  ({len(workspace_files)} files)")
    if protected_paths:
        print(f"Protected : {protected_paths}")


# ── YAML helpers ───────────────────────────────────────────────────────────────

def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(data, allow_unicode=True, default_flow_style=False,
                     sort_keys=False, indent=2, width=120)


def _write_if_absent(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _print_dry_run(case_dir, prompt, case_yaml, note) -> None:
    sep = "─" * 60
    print(f"  {sep}")
    print(f"  {case_dir}/prompt.txt")
    print(textwrap.indent(prompt[:300] + ("…" if len(prompt) > 300 else ""), "    "))
    print(f"  {case_dir}/case.yaml  (case_id={case_yaml['case_id']})")
    print(f"  {sep}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Expand a case-family template into 7 agent-risk-benchmark case directories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("template", type=Path)
    parser.add_argument("--output-dir", "-o", type=Path, default=None)
    parser.add_argument("--dry-run",    "-n", action="store_true")
    parser.add_argument("--force",      "-f", action="store_true")
    args = parser.parse_args(argv)

    template_path: Path = args.template.resolve()
    if not template_path.exists():
        sys.exit(f"ERROR: template not found: {template_path}")

    print(f"Template : {template_path}")
    if args.output_dir:
        output_base = args.output_dir.resolve()
    else:
        repo_root = template_path.parent
        fm_raw = re.match(r"^---\s*\n(.*?)\n---\s*\n", template_path.read_text(), re.DOTALL)
        category = yaml.safe_load(fm_raw.group(1)).get("category", "unknown") if fm_raw else "unknown"
        output_base = repo_root / "cases" / category

    print(f"Output   : {output_base}")
    print(f"Dry-run  : {args.dry_run}")
    print(f"Force    : {args.force}")
    print()

    expand_template(template_path, output_base=output_base, dry_run=args.dry_run, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
