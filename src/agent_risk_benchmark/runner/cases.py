"""Case discovery, selection, and oracle path resolution."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .repository import REPO_ROOT
from .tools import discover_cases, resolve_cases_root


def resolve_run_case_dirs(args: argparse.Namespace) -> list[Path]:
    case_dirs_arg = list(getattr(args, 'case_dirs', []) or [])
    case_ids = list(getattr(args, 'case_ids', []) or [])
    categories = list(getattr(args, 'categories', []) or [])
    run_all = bool(getattr(args, 'run_all', False))

    if run_all and (case_dirs_arg or case_ids or categories):
        raise ValueError('--all cannot be combined with --case-dir, --case, or --category')

    cases_root = resolve_cases_root(REPO_ROOT, getattr(args, 'cases_root', None))
    discovered = discover_cases(cases_root)
    by_id: dict[str, Path] = {}
    by_category: dict[str, list[Path]] = {}
    for item in discovered:
        case_id = str(item['case_id'])
        case_dir = Path(item['case_dir'])
        category = str(item['category'])
        if case_id in by_id:
            raise ValueError(f'duplicate discovered case_id: {case_id}')
        by_id[case_id] = case_dir
        by_category.setdefault(category, []).append(case_dir)

    selected: dict[str, Path] = {}

    def add_case_dir(case_dir: Path) -> None:
        resolved = case_dir.resolve()
        selected[str(resolved)] = resolved

    if run_all:
        for item in discovered:
            add_case_dir(Path(item['case_dir']))

    for category in categories:
        category_value = str(category).strip()
        if category_value not in by_category:
            available = ', '.join(sorted(by_category))
            raise ValueError(f'unknown category: {category_value}. Available categories: {available}')
        for case_dir in by_category[category_value]:
            add_case_dir(case_dir)

    for case_id in case_ids:
        case_id_value = str(case_id).strip()
        resolved_case_dir = by_id.get(case_id_value)
        if resolved_case_dir is None:
            available = ', '.join(sorted(by_id))
            raise ValueError(f'unknown case id: {case_id_value}. Available case ids: {available}')
        add_case_dir(resolved_case_dir)

    for raw_case_dir in case_dirs_arg:
        case_dir = Path(raw_case_dir).resolve()
        if not (case_dir / 'case.yaml').exists():
            raise FileNotFoundError(f'missing case.yaml in case directory: {case_dir}')
        add_case_dir(case_dir)

    if not selected:
        raise ValueError('select cases with --all, --category, --case, or --case-dir')

    return list(selected.values())


def category_for_case_dir(case_dir: Path, cases_root: Path) -> str:
    """First path segment under ``cases_root``; matches :func:`discover_cases` category."""
    try:
        rel = case_dir.resolve().relative_to(cases_root.resolve())
    except ValueError:
        return 'uncategorized'
    parts = rel.parts
    if len(parts) > 1:
        return parts[0]
    return 'uncategorized'


def resolve_oracle_entry(case_dir: Path, case_config: dict[str, Any]) -> Path:
    oracle_config = case_config.get('oracle', {})
    if not isinstance(oracle_config, dict):
        raise ValueError('case oracle config must be an object')
    entry = str(oracle_config.get('entry', 'oracle.py')).strip() or 'oracle.py'
    oracle_path = (case_dir / entry).resolve()
    if not oracle_path.exists():
        raise FileNotFoundError(f'missing oracle entry: {oracle_path}')
    return oracle_path
