"""Shared helpers: case discovery, progress logging, trace tokens, summary I/O."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# argparse: bare ``--summary`` (no PATH) sets this const so we pick a default filename.
SUMMARY_CLI_SENTINEL = '__SUMMARY_DEFAULT_PATH__'


def emit_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def format_progress(case_id: str, phase: str, *, index: int | None = None, total: int | None = None) -> str:
    if index is not None and total is not None:
        return f'[{index}/{total}] {case_id}: {phase}'
    return f'{case_id}: {phase}'


def load_case_config(case_dir: Path) -> dict[str, Any]:
    return yaml.safe_load((case_dir / 'case.yaml').read_text(encoding='utf-8'))


def discover_cases(cases_root: Path) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    for case_file in sorted(cases_root.rglob('case.yaml')) if cases_root.exists() else []:
        case_dir = case_file.parent
        if case_dir.name.startswith('.') or case_dir.name == '__pycache__':
            continue

        case_config = load_case_config(case_dir)
        case_id = str(case_config.get('case_id', '')).strip()
        if not case_id:
            raise ValueError(f'missing case_id in {case_file}')

        rel_parts = case_dir.relative_to(cases_root).parts
        category = rel_parts[0] if len(rel_parts) > 1 else 'uncategorized'
        discovered.append(
            {
                'case_id': case_id,
                'category': category,
                'case_dir': case_dir.resolve(),
            }
        )
    return discovered


def resolve_cases_root(repo_root: Path, cases_root_arg: str | None) -> Path:
    raw = (cases_root_arg or 'cases').strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (repo_root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.exists():
        raise FileNotFoundError(f'cases root not found: {candidate}')
    if not candidate.is_dir():
        raise ValueError(f'cases root is not a directory: {candidate}')
    return candidate


def summarize_batch_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts, token usage, and durations by category and overall."""

    def new_bucket() -> dict[str, Any]:
        return {
            'count': 0,
            'error_count': 0,
            'completed_count': 0,
            'task_success_count': 0,
            'safety_success_count': 0,
            'full_success_count': 0,
            'clarification_count': 0,
            'clarification_n': 0,
            'clarification_quality_sum': 0.0,
            'clarification_quality_n': 0,
            'blocked_action_count': 0,
            'blocked_action_n': 0,
            'redundant_tool_call_sum': 0.0,
            'redundant_tool_call_n': 0,
            'redundant_tool_call_average_sum': 0.0,
            'redundant_tool_call_average_n': 0,
            'score_sum': 0.0,
            'score_n': 0,
            'process_score_sum': 0.0,
            'process_score_n': 0,
            'tool_efficiency_score_sum': 0.0,
            'tool_efficiency_score_n': 0,
            'safety_score_sum': 0.0,
            'safety_score_n': 0,
            'reasoning_pct_sum': 0.0,
            'reasoning_pct_n': 0,
            'token_usage': {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'total': 0},
            'model_turn_count': 0,
            'execute_duration_sec': 0.0,
            'http_duration_sec': 0.0,
        }

    def ingest(bucket: dict[str, Any], r: dict[str, Any]) -> None:
        bucket['count'] += 1
        if 'error' in r:
            bucket['error_count'] += 1
        else:
            bucket['completed_count'] += 1
        if r.get('task_success'):
            bucket['task_success_count'] += 1
        if r.get('safety_success'):
            bucket['safety_success_count'] += 1
        if r.get('task_success') and r.get('safety_success'):
            bucket['full_success_count'] += 1
        if 'error' not in r and r.get('clarification_attempted') is not None:
            bucket['clarification_n'] += 1
            if r.get('clarification_attempted'):
                bucket['clarification_count'] += 1
        if 'error' not in r and r.get('clarification_quality') is not None:
            bucket['clarification_quality_sum'] += float(r.get('clarification_quality') or 0)
            bucket['clarification_quality_n'] += 1
        if 'error' not in r and r.get('blocked_action') is not None:
            bucket['blocked_action_n'] += 1
            if r.get('blocked_action'):
                bucket['blocked_action_count'] += 1
        if 'error' not in r and r.get('redundant_tool_call_count') is not None:
            bucket['redundant_tool_call_sum'] += float(r.get('redundant_tool_call_count') or 0)
            bucket['redundant_tool_call_n'] += 1
        if 'error' not in r:
            redundant_average = r.get('tool_call_average')
            if redundant_average is None:
                redundant_average = r.get('redundant_tool_call_average')
            if redundant_average is None:
                redundant_count = r.get('redundant_tool_call_count')
                redundant_average = float(redundant_count) if redundant_count is not None else None
            if redundant_average is not None:
                bucket['redundant_tool_call_average_sum'] += float(redundant_average)
                bucket['redundant_tool_call_average_n'] += 1
        tu = r.get('token_usage') or {}
        for key in ('input', 'output', 'cache_read', 'cache_write', 'total'):
            bucket['token_usage'][key] += int(tu.get(key, 0))
        bucket['model_turn_count'] += int(r.get('model_turn_count', 0) or 0)
        bucket['execute_duration_sec'] += float(r.get('execute_duration_sec', 0) or 0)
        bucket['http_duration_sec'] += float(r.get('http_duration_sec', 0) or 0)
        if 'error' not in r and r.get('score') is not None:
            bucket['score_sum'] += float(r.get('score', 0) or 0)
            bucket['score_n'] += 1
        if 'error' not in r:
            process_score = r.get('process_score')
            if process_score is None:
                process_score = r.get('task_score')
            if process_score is None:
                process_score = 1.0 if r.get('task_success') else 0.0
            bucket['process_score_sum'] += float(process_score)
            bucket['process_score_n'] += 1

            efficiency_score = r.get('tool_efficiency_score')
            if efficiency_score is not None:
                bucket['tool_efficiency_score_sum'] += float(efficiency_score)
                bucket['tool_efficiency_score_n'] += 1

            safety_score = r.get('safety_score')
            if safety_score is None:
                safety_score = 1.0 if r.get('safety_success') else 0.0
            bucket['safety_score_sum'] += float(safety_score)
            bucket['safety_score_n'] += 1

    by_category: dict[str, dict[str, Any]] = {}
    overall = new_bucket()
    for r in results:
        cat = str(r.get('category') or 'uncategorized').strip() or 'uncategorized'
        if cat not in by_category:
            by_category[cat] = new_bucket()
        ingest(by_category[cat], r)
        ingest(overall, r)

    def finalize(bucket: dict[str, Any]) -> dict[str, Any]:
        n = bucket['count']
        tu = {k: int(v) for k, v in bucket['token_usage'].items()}
        return {
            'count': n,
            'error_count': bucket['error_count'],
            'completed_count': bucket['completed_count'],
            'task_success_count': bucket['task_success_count'],
            'safety_success_count': bucket['safety_success_count'],
            'full_success_count': bucket['full_success_count'],
            'clarification_count': bucket['clarification_count'],
            'clarification_n': bucket['clarification_n'],
            'clarification_quality_n': bucket['clarification_quality_n'],
            'mean_clarification_quality': round(
                bucket['clarification_quality_sum'] / bucket['clarification_quality_n'],
                4,
            )
            if bucket['clarification_quality_n']
            else None,
            'blocked_action_count': bucket['blocked_action_count'],
            'blocked_action_n': bucket['blocked_action_n'],
            'redundant_tool_call_n': bucket['redundant_tool_call_n'],
            'mean_redundant_tool_call_count': round(
                bucket['redundant_tool_call_sum'] / bucket['redundant_tool_call_n'],
                4,
            )
            if bucket['redundant_tool_call_n']
            else None,
            'redundant_tool_call_average_n': bucket['redundant_tool_call_average_n'],
            'mean_redundant_tool_call_average': round(
                bucket['redundant_tool_call_average_sum'] / bucket['redundant_tool_call_average_n'],
                4,
            )
            if bucket['redundant_tool_call_average_n']
            else None,
            'task_success_rate': round(bucket['task_success_count'] / n, 4) if n else 0.0,
            'safety_success_rate': round(bucket['safety_success_count'] / n, 4) if n else 0.0,
            'full_success_rate': round(bucket['full_success_count'] / n, 4) if n else 0.0,
            'clarification_rate': round(bucket['clarification_count'] / bucket['clarification_n'], 4)
            if bucket['clarification_n']
            else None,
            'mean_process_score': round(bucket['process_score_sum'] / bucket['process_score_n'], 4)
            if bucket['process_score_n']
            else None,
            'mean_tool_efficiency_score': round(
                bucket['tool_efficiency_score_sum'] / bucket['tool_efficiency_score_n'],
                4,
            )
            if bucket['tool_efficiency_score_n']
            else None,
            'mean_safety_score': round(bucket['safety_score_sum'] / bucket['safety_score_n'], 4)
            if bucket['safety_score_n']
            else None,
            'mean_score': round(bucket['score_sum'] / bucket['score_n'], 4) if bucket['score_n'] else None,
            'token_usage': tu,
            'model_turn_count': int(bucket['model_turn_count']),
            'execute_duration_sec': round(bucket['execute_duration_sec'], 3),
            'http_duration_sec': round(bucket['http_duration_sec'], 3),
        }

    return {
        'by_category': {k: finalize(v) for k, v in sorted(by_category.items())},
        'overall': finalize(overall),
    }


def format_batch_rollup_text(rollups: dict[str, Any]) -> str:
    """Human-readable category breakdown + overall (for container / logs)."""
    lines: list[str] = []
    by_cat = rollups.get('by_category') or {}
    overall = rollups.get('overall') or {}
    if not by_cat and not overall.get('count'):
        return ''

    lines.append('--- Summary by category ---')
    if by_cat:
        cat_w = max(len(c) for c in by_cat)
        cat_w = max(cat_w, len('category'))
        hdr = (
            f"{'category':<{cat_w}}   n  err  task_ok safe_ok full_ok clar_ok clar_q blocked tool_avg  "
            f"proc_sc eff_sc safe_sc mean_sc  "
            f"tok_in  tok_out  tok_total  turns  exec_s"
        )
        lines.append(hdr)
        lines.append('-' * len(hdr))
        for name, b in by_cat.items():
            tu = b.get('token_usage') or {}
            mts = b.get('mean_process_score')
            mes = b.get('mean_tool_efficiency_score')
            mss = b.get('mean_safety_score')
            ms = b.get('mean_score')
            mts_s = f'{mts:.2f}' if mts is not None else '-'
            mes_s = f'{mes:.2f}' if mes is not None else '-'
            mss_s = f'{mss:.2f}' if mss is not None else '-'
            ms_s = f'{ms:.2f}' if ms is not None else '-'
            clar_n = int(b.get('clarification_n') or 0)
            clar_s = f'{b.get("clarification_count", 0)}/{clar_n}' if clar_n else '-'
            clar_q = b.get('mean_clarification_quality')
            clar_q_s = f'{clar_q:.2f}' if clar_q is not None else '-'
            block_n = int(b.get('blocked_action_n') or 0)
            block_s = f'{b.get("blocked_action_count", 0)}/{block_n}' if block_n else '-'
            redun = b.get('mean_redundant_tool_call_average')
            redun_s = f'{redun:.2f}' if redun is not None else '-'
            lines.append(
                f'{name:<{cat_w}}  {b["count"]:>3}  {b["error_count"]:>3}  '
                f'{b["task_success_count"]:>7}  {b["safety_success_count"]:>7}  {b["full_success_count"]:>7} {clar_s:>7} {clar_q_s:>6} {block_s:>7} {redun_s:>9}  '
                f'{mts_s:>7} {mes_s:>6} {mss_s:>7} {ms_s:>7}  '
                f'{tu.get("input", 0):>7}  {tu.get("output", 0):>8}  '
                f'{tu.get("total", 0):>9}  {b.get("model_turn_count", 0):>5}  '
                f'{b["execute_duration_sec"]:>6.1f}'
            )
    else:
        lines.append('(no per-category rows)')

    lines.append('')
    lines.append('--- Overall ---')
    tu = overall.get('token_usage') or {}
    mts = overall.get('mean_process_score')
    mes = overall.get('mean_tool_efficiency_score')
    mss = overall.get('mean_safety_score')
    ms = overall.get('mean_score')
    mts_s = f'{mts:.2f}' if mts is not None else '-'
    mes_s = f'{mes:.2f}' if mes is not None else '-'
    mss_s = f'{mss:.2f}' if mss is not None else '-'
    ms_s = f'{ms:.2f}' if ms is not None else '-'
    n = int(overall.get('count') or 0)
    clar_n = int(overall.get('clarification_n') or 0)
    clar_s = f'{overall.get("clarification_count", 0)}/{clar_n}' if clar_n else '-'
    clar_q = overall.get('mean_clarification_quality')
    clar_q_s = f'{clar_q:.2f}' if clar_q is not None else '-'
    block_n = int(overall.get('blocked_action_n') or 0)
    block_s = f'{overall.get("blocked_action_count", 0)}/{block_n}' if block_n else '-'
    redun = overall.get('mean_redundant_tool_call_average')
    redun_s = f'{redun:.2f}' if redun is not None else '-'
    lines.append(
        f'cases={n}  errors={overall.get("error_count", 0)}  '
        f'task_success={overall.get("task_success_count", 0)}/{n}  '
        f'safety_success={overall.get("safety_success_count", 0)}/{n}  '
        f'full_success={overall.get("full_success_count", 0)}/{n}  '
        f'clarification={clar_s}  '
        f'clarification_quality={clar_q_s}  '
        f'blocked_action={block_s}  '
        f'tool_calls_per_tool={redun_s}  '
        f'process_score={mts_s}  '
        f'tool_efficiency_score={mes_s}  '
        f'safety_score={mss_s}  '
        f'mean_score={ms_s}  '
        f'model_turns={overall.get("model_turn_count", 0)}'
    )
    if tu.get('total') or tu.get('input') or tu.get('output'):
        lines.append(
            f'tokens  in={tu.get("input", 0)}  out={tu.get("output", 0)}  '
            f'total={tu.get("total", 0)}'
        )
    lines.append(
        f'execute_sec={overall.get("execute_duration_sec", 0)}  '
        f'http_sec={overall.get("http_duration_sec", 0)}'
    )
    return '\n'.join(lines)


def format_pass_at_k_summary_markdown(document: dict[str, Any]) -> str:
    """Readable report for pass-at-k rollup + per-case aggregates (Markdown)."""
    lines: list[str] = [
        '# pass-at-k summary',
        f'Generated (UTC): {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}Z',
        '',
    ]
    for key in (
        'run_date',
        'runs_partition',
        'model_label',
        'n_trials',
        'sample_k',
        'metric',
        'score_threshold',
        'replicates',
    ):
        if key in document and document[key] is not None:
            lines.append(f'- **{key}:** {document[key]!r}' if key == 'replicates' else f'- **{key}:** {document[key]}')
    mph = document.get('rollup', {}).get('mean_pass_at_k_hypergeom')
    mpp = document.get('rollup', {}).get('mean_pass_pow_k_hypergeom')
    if mph is not None:
        lines.append(f'- **mean pass@k (hypergeom, over cases):** {mph:.4f}')
    if mpp is not None:
        lines.append(f'- **mean pass^k (hypergeom, over cases):** {mpp:.4f}')
    lines.append(
        f'- **pass@k (discrete):** {document.get("pass_at_k_count", 0)}/{document.get("case_count", 0)} '
        f'({document.get("pass_at_k_rate", 0.0):.4f})'
    )
    lines.append(
        f'- **pass_all_k (discrete):** {document.get("pass_all_k_count", 0)}/{document.get("case_count", 0)} '
        f'({document.get("pass_all_k_rate", 0.0):.4f})'
    )
    lines.append('')

    roll = document.get('rollup') or {}
    tu = roll.get('total_token_usage') or {}
    lines.append('## Rollup (all trials)')
    lines.append('')
    lines.append(
        f'- **trial_slots:** {roll.get("trial_slots", 0)} '
        f'(expected {roll.get("expected_trial_slots", 0)} = cases × n_trials)'
    )
    lines.append(f'- **trials_with_score.json:** {roll.get("trials_with_score_json", 0)}')
    lines.append(f'- **missing_score.json slots:** {roll.get("missing_score_slots", 0)}')
    mhttp = roll.get('mean_http_duration_sec')
    if mhttp is not None:
        lines.append(
            f'- **mean http_duration_sec** (OpenClaw chat request, where present): {mhttp:.4f}'
        )
    else:
        lines.append('- **mean http_duration_sec:** —')
    mex = roll.get('mean_execute_duration_sec')
    if mex is not None:
        lines.append(f'- **mean execute_duration_sec** (from metadata when present): {mex:.4f}')
    lines.append(f'- **trials_with_http_latency:** {roll.get("trials_with_http_latency", 0)}')
    lines.append(f'- **trials_with_execute_duration:** {roll.get("trials_with_execute_duration", 0)}')
    msteps = roll.get('mean_trace_steps_per_trial')
    if msteps is not None:
        lines.append(f'- **mean trace steps per trial (JSONL lines):** {msteps:.4f}')
    lines.append(f'- **total trace steps:** {roll.get("total_trace_steps", 0)}')
    mturns = roll.get('mean_model_turns_per_trial')
    if mturns is not None:
        lines.append(f'- **mean model turns per trial:** {mturns:.4f}')
    lines.append(f'- **total model turns:** {roll.get("total_model_turns", 0)}')
    lines.append(
        f'- **tokens (sum over trials):** in={tu.get("input", 0)}  out={tu.get("output", 0)}  '
        f'total={tu.get("total", 0)}'
    )
    mprog = roll.get('mean_task_progress')
    if mprog is not None:
        lines.append(
            f'- **mean task_progress** (mean of ``reasoning_progress_pct``/100 per case; over cases): '
            f'{mprog:.4f}'
        )
    lines.append('')

    lines.append('## Per case')
    lines.append('')
    lines.append(
        '| case_id | c/n | pass@k_h | pass^k_h | discrete@k | discrete_all | task_prog | '
        'tok_total | turns | trace_steps | mean http_s |'
    )
    lines.append('|---|---|---|---|---|---|---|---|---|---|---|')
    for row in document.get('per_case') or []:
        cid = str(row.get('case_id', ''))
        n_t = int(row.get('n_trials') or 0)
        c_s = int(row.get('c_success') or 0)
        cn = f'{c_s}/{n_t}' if n_t else '—'
        pkh = row.get('pass_at_k_hypergeom')
        ppkh = row.get('pass_pow_k_hypergeom')
        pkh_s = f'{pkh:.3f}' if pkh is not None else '—'
        ppkh_s = f'{ppkh:.3f}' if ppkh is not None else '—'
        pk = '✓' if row.get('pass_at_k') else '✗'
        pak = '✓' if row.get('pass_all_k') else '✗'
        orates = row.get('outcome_rates') or {}
        tp = orates.get('task_progress')
        tp_s = f'{tp:.3f}' if tp is not None else '—'
        agg = row.get('aggregates') or {}
        tok_sum = agg.get('token_usage_sum') or {}
        tt = int(tok_sum.get('total', 0))
        mt = int(agg.get('model_turn_count_sum') or 0)
        st = int(agg.get('trace_step_count_sum') or 0)
        mh = agg.get('mean_http_duration_sec')
        mh_s = f'{mh:.3f}' if mh is not None else '—'
        lines.append(
            f'| {cid} | {cn} | {pkh_s} | {ppkh_s} | {pk} | {pak} | {tp_s} | '
            f'{tt} | {mt} | {st} | {mh_s} |'
        )
    lines.append('')
    lines.append('Trial-level task/safety/score/tokens/http are in the JSON `per_case[].trials[]`.')
    return '\n'.join(lines)


def empty_token_usage() -> dict[str, int]:
    return {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'total': 0}


def comb_or_zero(n: int, k: int) -> int:
    """Binomial coefficient C(n,k); 0 if k < 0 or k > n."""
    if k < 0 or k > n:
        return 0
    return math.comb(n, k)


def pass_at_k_hypergeometric(n: int, c: int, k: int) -> float:
    """Unbiased pass@k from n trials with c successes (without-replacement draw of k from n).

    P(at least one success in k draws) = 1 - C(n-c, k) / C(n, k).
    """
    den = comb_or_zero(n, k)
    if den == 0:
        return 0.0
    return 1.0 - (comb_or_zero(n - c, k) / den)


def pass_pow_k_hypergeometric(n: int, c: int, k: int) -> float:
    """Pass^k: P(all k draws are successes) = C(c, k) / C(n, k)."""
    den = comb_or_zero(n, k)
    if den == 0:
        return 0.0
    return comb_or_zero(c, k) / den


def count_trace_steps(trace_path: Path | None) -> int:
    """Count all non-empty trace JSONL records; this is not an LLM turn count."""
    if trace_path is None or not Path(trace_path).exists():
        return 0
    return sum(1 for line in Path(trace_path).read_text(encoding='utf-8').splitlines() if line.strip())


def count_model_turns(trace_path: Path | None) -> int:
    """Count assistant response records in a canonical trace.

    OpenClaw attaches usage to every assistant response, while some normalized
    backends append one aggregate usage-only record. Prefer content-bearing assistant
    messages so the latter is not mistaken for a single-turn run; use usage records
    only as a fallback for traces without assistant content.
    """
    if trace_path is None or not Path(trace_path).exists():
        return 0
    assistant_turns = 0
    usage_records = 0
    for raw in Path(trace_path).read_text(encoding='utf-8').splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        usage = record.get('usage')
        if isinstance(usage, dict):
            usage_records += 1
        message = record.get('message')
        if not isinstance(message, dict):
            continue
        role = str(message.get('role') or '').lower()
        if role == 'assistant' and message.get('content'):
            assistant_turns += 1
        if isinstance(message.get('usage'), dict):
            usage_records += 1
    return assistant_turns or usage_records


def _accumulate_usage_dict(totals: dict[str, int], usage: dict[str, Any]) -> None:
    """Add one usage object using canonical input/output/total token semantics."""
    inp = usage.get('input', usage.get('prompt_tokens'))
    out = usage.get('output', usage.get('completion_tokens'))
    input_tokens = int(inp if inp is not None else 0)
    output_tokens = int(out if out is not None else 0)
    reported_total = usage.get('totalTokens', usage.get('total_tokens', usage.get('total')))

    # OpenClaw can report ``input`` as only the uncached prompt portion while
    # ``totalTokens`` includes cached prompt tokens. Canonical input is all
    # prompt tokens, so derive it from total when the provider exposes both.
    if reported_total is not None:
        total_tokens = int(reported_total)
        if total_tokens >= output_tokens:
            input_tokens = total_tokens - output_tokens
        else:
            total_tokens = input_tokens + output_tokens
    else:
        total_tokens = input_tokens + output_tokens

    totals['input'] += input_tokens
    totals['output'] += output_tokens
    totals['cache_read'] += int(usage.get('cacheRead', usage.get('cache_read', 0)) or 0)
    totals['cache_write'] += int(usage.get('cacheWrite', usage.get('cache_write', 0)) or 0)
    totals['total'] += total_tokens


def sum_trace_tokens(trace_path: Path | None) -> dict[str, int]:
    """Sum token usage across all turns recorded in an openclaw trace JSONL file.

    Each turn may carry a ``usage`` object at the top level or inside ``message``.
    Recognised keys include OpenClaw-style ``input`` / ``output`` / ``cacheRead`` /
    ``cacheWrite`` / ``totalTokens``, and OpenAI-style ``prompt_tokens`` /
    ``completion_tokens`` / ``total_tokens`` (common for some providers in traces).
    ``input`` includes cached and uncached prompt tokens, and ``total`` equals
    ``input + output``. Cache fields remain available as internal diagnostics.
    """
    totals: dict[str, int] = {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'total': 0}
    if trace_path is None or not Path(trace_path).exists():
        return totals
    for raw in Path(trace_path).read_text(encoding='utf-8').splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        usage = record.get('usage')
        if not isinstance(usage, dict):
            msg = record.get('message')
            usage = msg.get('usage') if isinstance(msg, dict) else None
        if not isinstance(usage, dict):
            continue
        _accumulate_usage_dict(totals, usage)
    if totals['total'] <= 0:
        totals['total'] = totals['input'] + totals['output']
    return totals


def collect_trial_run_metrics(run_dir: Path) -> dict[str, Any]:
    """Best-effort HTTP latency + token totals from artifacts under a trial run directory."""
    http_sec: float | None = None
    resp_path = run_dir / 'openclaw_response.json'
    if resp_path.is_file():
        try:
            payload = json.loads(resp_path.read_text(encoding='utf-8'))
            if isinstance(payload, dict) and payload.get('duration_sec') is not None:
                http_sec = round(float(payload['duration_sec']), 6)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    execute_sec: float | None = None
    trace_path: Path | None = None
    meta_path = run_dir / 'metadata.json'
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            if isinstance(meta, dict):
                if meta.get('execute_duration_sec') is not None:
                    try:
                        execute_sec = round(float(meta['execute_duration_sec']), 6)
                    except (TypeError, ValueError):
                        pass
                otp = meta.get('openclaw_trace_file')
                if otp:
                    cand = Path(str(otp))
                    if cand.is_file():
                        trace_path = cand
        except (OSError, json.JSONDecodeError):
            pass
    if trace_path is None:
        bench = run_dir / 'bench-run.jsonl'
        if bench.is_file():
            trace_path = bench
        else:
            jsonls = sorted(run_dir.glob('*.jsonl'))
            if jsonls:
                trace_path = jsonls[0]

    token_usage = sum_trace_tokens(trace_path)
    trace_step_count = count_trace_steps(trace_path)
    model_turn_count = count_model_turns(trace_path)
    return {
        'http_duration_sec': http_sec,
        'execute_duration_sec': execute_sec,
        'token_usage': token_usage,
        'trace_step_count': trace_step_count,
        'model_turn_count': model_turn_count,
    }


def compute_trial_outcome_rates(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-case rates from non-missing trials: task/safety/mean_score and task_progress."""
    scored = [t for t in trials if not t.get('missing')]
    if not scored:
        return {
            'trial_count_scored': 0,
            'mean_task_success_rate': None,
            'mean_safety_success_rate': None,
            'mean_score': None,
            'task_progress': None,
        }
    n = len(scored)
    task_n = sum(1 for t in scored if t.get('task_success'))
    safe_n = sum(1 for t in scored if t.get('safety_success'))
    scores: list[float] = []
    for t in scored:
        if t.get('score') is not None:
            try:
                scores.append(float(t['score']))
            except (TypeError, ValueError):
                pass
    mean_sc = sum(scores) / len(scores) if scores else None
    mt = task_n / n
    ms = safe_n / n
    rp_ratios: list[float] = []
    for t in scored:
        pct = t.get('reasoning_progress_pct')
        if pct is None:
            continue
        try:
            rp_ratios.append(float(pct) / 100.0)
        except (TypeError, ValueError):
            continue
    if rp_ratios:
        task_progress = sum(rp_ratios) / len(rp_ratios)
    else:
        task_progress = mean_sc if mean_sc is not None else mt
    return {
        'trial_count_scored': n,
        'mean_task_success_rate': round(mt, 6),
        'mean_safety_success_rate': round(ms, 6),
        'mean_score': round(mean_sc, 6) if mean_sc is not None else None,
        'task_progress': round(task_progress, 6),
    }


def load_score_json(run_dir: Path) -> dict[str, Any] | None:
    """Load ``score.json`` from a trial run directory, if present."""
    p = run_dir / 'score.json'
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def mean_reasoning_progress_ratio_from_score_dicts(
    score_dicts: list[dict[str, Any]],
) -> float | None:
    """Mean of oracle ``reasoning_progress_pct / 100`` (ordered-step coverage, 0–1 scale).

    Only entries with a numeric ``reasoning_progress_pct`` are included. Returns
    ``None`` if none are present (caller should fall back to legacy task_progress).
    """
    ratios: list[float] = []
    for sc in score_dicts:
        pct = sc.get('reasoning_progress_pct')
        if pct is None:
            continue
        try:
            ratios.append(float(pct) / 100.0)
        except (TypeError, ValueError):
            continue
    if not ratios:
        return None
    return round(sum(ratios) / len(ratios), 6)


def trial_satisfies_metric(score: dict[str, Any], metric: str, score_threshold: float) -> bool:
    """Whether a scored run counts as success for pass metrics."""
    if metric == 'full':
        return bool(score.get('task_success')) and bool(score.get('safety_success'))
    if metric == 'task':
        return bool(score.get('task_success'))
    if metric == 'safety':
        return bool(score.get('safety_success'))
    if metric == 'score':
        try:
            return float(score.get('score', 0)) >= score_threshold
        except (TypeError, ValueError):
            return False
    raise ValueError(f'unknown metric: {metric}')


def empty_pass_trial_metrics() -> dict[str, Any]:
    return {
        'http_duration_sec': None,
        'execute_duration_sec': None,
        'token_usage': empty_token_usage(),
        'trace_step_count': 0,
        'model_turn_count': 0,
    }


def build_pass_metrics_document(
    *,
    run_date: str,
    runs_partition: Path,
    metric: str,
    score_threshold: float,
    sample_k: int | None,
    model_label: str | None = None,
    source: str = 'pass_at_k',
    replicates: list[str] | None = None,
    case_roots: list[Path] | None = None,
    case_run_paths: dict[str, list[Path]] | None = None,
) -> dict[str, Any]:
    """Build the same structure as the ``pass-at-k`` CLI JSON (hypergeom + discrete + rollup).

    Supply either ``case_run_paths`` (case_id → ordered trial run dirs) or both ``case_roots``
    and ``replicates`` (run directory names under each case).
    """
    if case_run_paths is not None:
        ordered = sorted(case_run_paths.items(), key=lambda x: x[0])
        if not ordered:
            n_trials = 0
            replicates_out: list[str] = []
            iter_cases: list[tuple[str, list[Path]]] = []
        else:
            n_trials = len(ordered[0][1])
            replicates_out = [p.name for p in ordered[0][1]]
            for cid, paths in ordered:
                if len(paths) != n_trials:
                    raise ValueError(
                        f'inconsistent trial count for case {cid}: expected {n_trials}, got {len(paths)}'
                    )
            iter_cases = ordered
    elif case_roots is not None and replicates is not None:
        n_trials = len(replicates)
        replicates_out = list(replicates)
        iter_cases = [(cr.name, [cr / rn for rn in replicates]) for cr in sorted(case_roots)]
    else:
        raise ValueError('build_pass_metrics_document needs case_run_paths or (case_roots and replicates)')

    if n_trials < 1 or not iter_cases:
        return {
            'mode': 'pass_at_k',
            'source': source,
            'run_date': run_date,
            'runs_partition': str(runs_partition),
            'replicates': replicates_out,
            'n_trials': 0,
            'sample_k': 0,
            'metric': metric,
            'score_threshold': score_threshold,
            'case_count': 0,
            'pass_at_k_count': 0,
            'pass_at_k_rate': 0.0,
            'pass_all_k_count': 0,
            'pass_all_k_rate': 0.0,
            'missing_score_files': 0,
            'rollup': {},
            'per_case': [],
        }

    sk = int(sample_k) if sample_k is not None else n_trials
    if sk < 1:
        raise ValueError('--sample-k must be >= 1')
    if sk > n_trials:
        raise ValueError(f'sample_k ({sk}) must be <= n trials ({n_trials})')

    per_case: list[dict[str, Any]] = []
    pass_at_k_n = 0
    pass_all_k_n = 0
    missing_scores = 0

    for case_id, paths in iter_cases:
        trials: list[dict[str, Any]] = []
        outcomes: list[bool] = []
        for rd in paths:
            run_name = rd.name
            metrics = collect_trial_run_metrics(rd) if rd.is_dir() else empty_pass_trial_metrics()
            sc = load_score_json(rd) if rd.is_dir() else None
            if sc is None:
                missing_scores += 1
                outcomes.append(False)
                trials.append(
                    {
                        'run_name': run_name,
                        'success': False,
                        'missing': True,
                        'task_success': None,
                        'safety_success': None,
                        'score': None,
                        **metrics,
                    }
                )
            else:
                ok = trial_satisfies_metric(sc, metric, score_threshold)
                outcomes.append(ok)
                trials.append(
                    {
                        'run_name': run_name,
                        'success': ok,
                        'missing': False,
                        'task_success': bool(sc.get('task_success')),
                        'safety_success': bool(sc.get('safety_success')),
                        'score': sc.get('score'),
                        'reasoning_progress_pct': sc.get('reasoning_progress_pct'),
                        **metrics,
                    }
                )

        c_success = sum(outcomes)
        any_ok = c_success >= 1
        all_ok = c_success == n_trials
        pk_hyp = pass_at_k_hypergeometric(n_trials, c_success, sk)
        ppk_hyp = pass_pow_k_hypergeometric(n_trials, c_success, sk)
        if any_ok:
            pass_at_k_n += 1
        if all_ok:
            pass_all_k_n += 1
        per_case.append(
            {
                'case_id': case_id,
                'n_trials': n_trials,
                'c_success': c_success,
                'sample_k': sk,
                'pass_at_k_hypergeom': round(pk_hyp, 8),
                'pass_pow_k_hypergeom': round(ppk_hyp, 8),
                'trials': trials,
                'pass_at_k': any_ok,
                'pass_all_k': all_ok,
            }
        )

    n_cases = len(per_case)
    attach_pass_at_k_per_case_aggregates(per_case)
    for row in per_case:
        row['outcome_rates'] = compute_trial_outcome_rates(row['trials'])
    rollup = build_pass_at_k_rollup(
        per_case, trials_per_case=n_trials, n_cases=n_cases, sample_k=sk
    )

    doc: dict[str, Any] = {
        'mode': 'pass_at_k',
        'source': source,
        'run_date': run_date,
        'runs_partition': str(runs_partition),
        'replicates': replicates_out,
        'n_trials': n_trials,
        'sample_k': sk,
        'metric': metric,
        'score_threshold': score_threshold,
        'case_count': n_cases,
        'pass_at_k_count': pass_at_k_n,
        'pass_at_k_rate': round(pass_at_k_n / n_cases, 6) if n_cases else 0.0,
        'pass_all_k_count': pass_all_k_n,
        'pass_all_k_rate': round(pass_all_k_n / n_cases, 6) if n_cases else 0.0,
        'missing_score_files': missing_scores,
        'rollup': rollup,
        'per_case': per_case,
    }
    if model_label:
        doc['model_label'] = str(model_label).strip()
    return doc


def attach_pass_at_k_per_case_aggregates(per_case: list[dict[str, Any]]) -> None:
    """Mutate each per_case row with aggregates (token sums, mean http) across trials."""
    for row in per_case:
        tok = empty_token_usage()
        https: list[float] = []
        execs: list[float] = []
        steps_total = 0
        turns_total = 0
        for t in row.get('trials') or []:
            tu = t.get('token_usage') or {}
            for key in tok:
                tok[key] += int(tu.get(key, 0))
            hd = t.get('http_duration_sec')
            if hd is not None:
                https.append(float(hd))
            ed = t.get('execute_duration_sec')
            if ed is not None:
                execs.append(float(ed))
            steps_total += int(t.get('trace_step_count') or 0)
            turns_total += int(t.get('model_turn_count') or 0)
        row['aggregates'] = {
            'token_usage_sum': {k: int(v) for k, v in tok.items()},
            'mean_http_duration_sec': round(sum(https) / len(https), 6) if https else None,
            'mean_execute_duration_sec': round(sum(execs) / len(execs), 6) if execs else None,
            'total_execute_duration_sec': round(sum(execs), 6) if execs else None,
            'trace_step_count_sum': steps_total,
            'mean_trace_step_count': round(steps_total / len(row.get('trials') or []), 4)
            if row.get('trials')
            else None,
            'model_turn_count_sum': turns_total,
            'mean_model_turn_count': round(turns_total / len(row.get('trials') or []), 4)
            if row.get('trials')
            else None,
        }


def build_pass_at_k_rollup(
    per_case: list[dict[str, Any]],
    *,
    trials_per_case: int,
    n_cases: int,
    sample_k: int,
) -> dict[str, Any]:
    """Dataset-level token/latency/steps + mean hypergeom / outcome rates (one row per case)."""
    total_tok = empty_token_usage()
    https: list[float] = []
    execs: list[float] = []
    steps_all = 0
    turns_all = 0
    trial_slots = 0
    with_score = 0
    missing_slots = 0
    for row in per_case:
        for t in row.get('trials') or []:
            trial_slots += 1
            if t.get('missing'):
                missing_slots += 1
            else:
                with_score += 1
            tu = t.get('token_usage') or {}
            for key in total_tok:
                total_tok[key] += int(tu.get(key, 0))
            hd = t.get('http_duration_sec')
            if hd is not None:
                https.append(float(hd))
            ed = t.get('execute_duration_sec')
            if ed is not None:
                execs.append(float(ed))
            steps_all += int(t.get('trace_step_count') or 0)
            turns_all += int(t.get('model_turn_count') or 0)

    hyp_pk: list[float] = []
    hyp_ppk: list[float] = []
    mt_list: list[float] = []
    ms_list: list[float] = []
    mscore_list: list[float] = []
    prog_list: list[float] = []
    for row in per_case:
        if row.get('pass_at_k_hypergeom') is not None:
            hyp_pk.append(float(row['pass_at_k_hypergeom']))
        if row.get('pass_pow_k_hypergeom') is not None:
            hyp_ppk.append(float(row['pass_pow_k_hypergeom']))
        rates = row.get('outcome_rates') or {}
        if rates.get('mean_task_success_rate') is not None:
            mt_list.append(float(rates['mean_task_success_rate']))
        if rates.get('mean_safety_success_rate') is not None:
            ms_list.append(float(rates['mean_safety_success_rate']))
        if rates.get('mean_score') is not None:
            mscore_list.append(float(rates['mean_score']))
        if rates.get('task_progress') is not None:
            prog_list.append(float(rates['task_progress']))

    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 6) if xs else None

    return {
        'n_trials_per_case': trials_per_case,
        'sample_k': sample_k,
        'trial_slots': trial_slots,
        'expected_trial_slots': n_cases * trials_per_case if n_cases else 0,
        'trials_with_score_json': with_score,
        'missing_score_slots': missing_slots,
        'total_token_usage': {k: int(v) for k, v in total_tok.items()},
        'mean_http_duration_sec': round(sum(https) / len(https), 6) if https else None,
        'trials_with_http_latency': len(https),
        'mean_execute_duration_sec': round(sum(execs) / len(execs), 6) if execs else None,
        'total_execute_duration_sec': round(sum(execs), 6) if execs else None,
        'trials_with_execute_duration': len(execs),
        'total_trace_steps': steps_all,
        'mean_trace_steps_per_trial': round(steps_all / trial_slots, 6) if trial_slots else None,
        'total_model_turns': turns_all,
        'mean_model_turns_per_trial': round(turns_all / trial_slots, 6) if trial_slots else None,
        'mean_pass_at_k_hypergeom': mean(hyp_pk),
        'mean_pass_pow_k_hypergeom': mean(hyp_ppk),
        'mean_of_case_mean_task_success_rate': mean(mt_list),
        'mean_of_case_mean_safety_success_rate': mean(ms_list),
        'mean_of_case_mean_score': mean(mscore_list),
        'mean_task_progress': mean(prog_list),
    }


def resolve_pass_at_k_summary_path(
    args: argparse.Namespace,
    *,
    run_date: str,
    case_count: int,
    runs_root: Path,
) -> Path | None:
    if case_count == 0 or getattr(args, 'no_summary', False):
        return None
    raw = getattr(args, 'summary', None)
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if raw is None or raw == SUMMARY_CLI_SENTINEL:
        return (runs_root / run_date / f'pass_at_k_summary_{ts}.json').resolve()
    return Path(str(raw)).expanduser().resolve()


def resolve_summary_outpath(
    args: argparse.Namespace,
    *,
    run_date: str,
    mode_label: str,
    case_count: int,
    runs_root: Path,
) -> Path | None:
    """Target path for the written summary file, or None if ``--summary`` was not used."""
    if case_count == 0:
        return None
    raw = getattr(args, 'summary', None)
    if raw is None:
        return None
    if isinstance(raw, str) and raw and raw != SUMMARY_CLI_SENTINEL:
        return Path(raw).expanduser().resolve()
    if raw == SUMMARY_CLI_SENTINEL:
        ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        safe_mode = mode_label.replace('-', '_')
        return (runs_root / run_date / f'summary_{safe_mode}_{ts}.json').resolve()
    return None


def write_summary_file(path: Path, document: dict[str, Any]) -> Path:
    """Write ``document`` to ``path`` as JSON, or as Markdown if ``path`` ends with ``.md``."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    suff = path.suffix.lower()
    if suff == '.md':
        if document.get('mode') == 'pass_at_k':
            path.write_text(format_pass_at_k_summary_markdown(document) + '\n', encoding='utf-8')
            return path.resolve()
        rollup = document.get('batch_rollup')
        if rollup is None and document.get('results'):
            rollup = summarize_batch_results(document['results'])
        lines = [
            '# Summary',
            f'Generated (UTC): {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}Z',
            '',
        ]
        for key in (
            'mode',
            'run_date',
            'cases_root',
            'backend',
            'container_image',
            'model',
            'parallel',
            'num_worker',
            'wall_clock_sec',
            'batch_duration_sec',
        ):
            if key in document and document[key] is not None:
                lines.append(f'- **{key}:** {document[key]}')
        lines.append('')
        lines.append(format_batch_rollup_text(rollup or {}))
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path.resolve()

    json_path = path if suff == '.json' else path.with_suffix('.json')
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return json_path.resolve()
