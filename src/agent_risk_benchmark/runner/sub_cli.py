"""Handlers and orchestration for CLI subcommands."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path
from typing import Any

from .artifacts import (
    build_session_key,
    load_run_metadata,
    materialize_run,
    update_metadata,
    write_json,
    write_transcript,
)
from .cases import category_for_case_dir, resolve_run_case_dirs
from .constants import DEFAULT_REQUEST_TIMEOUT_SEC
from .evaluate import evaluate_run
from .openclaw.client import extract_assistant_text, invoke_openclaw_chat, parse_json_maybe
from .openclaw.config import (
    resolve_openclaw_settings,
    sync_openclaw_workspace,
    wait_for_openclaw_workspace_apply,
)
from .openclaw.trace import copy_openclaw_trace
from .repository import REPO_ROOT, RUNS_ROOT
from .tools import (
    build_pass_metrics_document,
    emit_progress,
    format_progress,
    load_case_config,
    resolve_cases_root,
    resolve_pass_at_k_summary_path,
    resolve_summary_outpath,
    sum_trace_tokens,
    summarize_batch_results,
    write_summary_file,
)


def prepare_run(case_dir: Path, args: argparse.Namespace) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    case_config, run_dir = materialize_run(case_dir, args.run_date, args.run_name)
    workspace_dir = (run_dir / 'workspace').resolve()
    openclaw_settings = resolve_openclaw_settings(args)
    synced_config_path = sync_openclaw_workspace(openclaw_settings['openclaw_config_path'], workspace_dir)
    workspace_apply_wait_sec = wait_for_openclaw_workspace_apply(openclaw_settings['openclaw_config_path'])
    summary = {
        'case_id': case_config['case_id'],
        'run_dir': str(run_dir),
        'workspace_dir': str(workspace_dir),
        'prompt_file': str((run_dir / 'prompt.txt').resolve()),
        'openclaw_config_path': synced_config_path,
        'openclaw_workspace_apply_wait_sec': workspace_apply_wait_sec,
        'next_step': 'Run the execute subcommand.',
    }
    return case_config, run_dir, summary


def prepare_command(args: argparse.Namespace) -> int:
    case_dir = Path(args.case_dir).resolve()
    _, _, summary = prepare_run(case_dir, args)
    print(json.dumps(summary, indent=2))
    return 0


def execute_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    exit_code, _ = execute_run(run_dir, args, emit_summary=True)
    return exit_code


def run_many(case_dirs: list[Path], args: argparse.Namespace) -> int:
    batch_started = time.time()
    results: list[dict[str, Any]] = []
    overall_exit_code = 0
    total = len(case_dirs)
    cases_root = resolve_cases_root(REPO_ROOT, getattr(args, 'cases_root', None))

    configured_workers = int(getattr(args, 'num_worker', 1) or 1)
    if configured_workers < 1:
        raise ValueError('--num-worker must be >= 1')

    # OpenClaw workspace path is synced through a shared config file.
    # Running with multiple workers against the same config can race and cross-contaminate workspaces.
    openclaw_settings = resolve_openclaw_settings(args)
    allow_unsafe_parallel = bool(getattr(args, 'allow_unsafe_parallel_openclaw', False))
    shared_openclaw_config = bool(openclaw_settings.get('openclaw_config_path'))
    if shared_openclaw_config and configured_workers > 1 and not allow_unsafe_parallel:
        emit_progress('Parallel execution disabled: shared OpenClaw config workspace sync is not thread-safe.')
        worker_count = 1
    else:
        worker_count = min(configured_workers, max(1, total))
        if shared_openclaw_config and configured_workers > 1 and allow_unsafe_parallel:
            emit_progress('Unsafe parallel mode enabled with shared OpenClaw config; workspace race conditions are possible.')

    def _run_single(idx: int, case_dir: Path) -> tuple[int, int, dict[str, Any]]:
        case_id = case_dir.name
        category = category_for_case_dir(case_dir, cases_root)
        try:
            emit_progress(format_progress(case_id, 'prepare', index=idx, total=total))
            case_config, run_dir, _ = prepare_run(case_dir, args)
            case_id = str(case_config['case_id'])
            emit_progress(format_progress(case_id, 'execute', index=idx, total=total))
            exit_code, summary = execute_run(run_dir, args, emit_summary=False)
            result = dict(summary)
            result['case_dir'] = str(case_dir)
            result['category'] = category
            result['exit_code'] = exit_code
            emit_progress(format_progress(case_id, f'complete (score={summary.get("score")})', index=idx, total=total))
            return idx, exit_code, result
        except Exception as exc:
            emit_progress(format_progress(case_id, f'failed ({type(exc).__name__}: {exc})', index=idx, total=total))
            return idx, 1, {
                'case_id': case_id,
                'case_dir': str(case_dir),
                'category': category,
                'task_success': False,
                'safety_success': False,
                'score': 0.0,
                'error': f'{type(exc).__name__}: {exc}',
            }

    if worker_count == 1:
        for idx, case_dir in enumerate(case_dirs, start=1):
            _, exit_code, result = _run_single(idx, case_dir)
            results.append(result)
            overall_exit_code = max(overall_exit_code, exit_code)
    else:
        indexed_results: dict[int, dict[str, Any]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_run_single, idx, case_dir) for idx, case_dir in enumerate(case_dirs, start=1)]
            for future in concurrent.futures.as_completed(futures):
                idx, exit_code, result = future.result()
                indexed_results[idx] = result
                overall_exit_code = max(overall_exit_code, exit_code)
        results = [indexed_results[i] for i in range(1, total + 1)]

    batch_elapsed = round(time.time() - batch_started, 3)
    batch_summary = {
        'mode': 'batch_run',
        'num_worker': worker_count,
        'requested_num_worker': configured_workers,
        'shared_openclaw_config': shared_openclaw_config,
        'allow_unsafe_parallel_openclaw': allow_unsafe_parallel,
        'selected_count': len(case_dirs),
        'completed_count': sum(1 for item in results if 'error' not in item),
        'error_count': sum(1 for item in results if 'error' in item),
        'task_success_count': sum(1 for item in results if bool(item.get('task_success'))),
        'safety_success_count': sum(1 for item in results if bool(item.get('safety_success'))),
        'full_success_count': sum(
            1
            for item in results
            if bool(item.get('task_success')) and bool(item.get('safety_success'))
        ),
        'batch_duration_sec': batch_elapsed,
        'total_http_duration_sec': round(sum(r.get('http_duration_sec', 0.0) for r in results), 3),
        'total_execute_duration_sec': round(sum(r.get('execute_duration_sec', 0.0) for r in results), 3),
        'total_token_usage': {
            'input': sum(r.get('token_usage', {}).get('input', 0) for r in results),
            'output': sum(r.get('token_usage', {}).get('output', 0) for r in results),
            'cache_read': sum(r.get('token_usage', {}).get('cache_read', 0) for r in results),
            'cache_write': sum(r.get('token_usage', {}).get('cache_write', 0) for r in results),
            'total': sum(r.get('token_usage', {}).get('total', 0) for r in results),
        },
        'batch_rollup': summarize_batch_results(results),
        'results': results,
    }
    summary_path = resolve_summary_outpath(
        args,
        run_date=str(args.run_date),
        mode_label='run',
        case_count=total,
        runs_root=RUNS_ROOT,
    )
    if summary_path:
        written = write_summary_file(summary_path, batch_summary)
        emit_progress(f'Wrote summary: {written}')
    print(json.dumps(batch_summary, indent=2))
    return overall_exit_code


def run_command(args: argparse.Namespace) -> int:
    case_dirs = resolve_run_case_dirs(args)
    if len(case_dirs) == 1:
        case_dir = case_dirs[0]
        case_id = case_dir.name
        emit_progress(format_progress(case_id, 'prepare', index=1, total=1))
        case_config, run_dir, _ = prepare_run(case_dir, args)
        case_id = str(case_config['case_id'])
        emit_progress(format_progress(case_id, 'execute', index=1, total=1))
        exit_code, summary = execute_run(run_dir, args, emit_summary=True)
        emit_progress(format_progress(case_id, f'complete (score={summary.get("score")})', index=1, total=1))
        summary_path = resolve_summary_outpath(
            args,
            run_date=str(args.run_date),
            mode_label='run',
            case_count=1,
            runs_root=RUNS_ROOT,
        )
        if summary_path:
            cases_root = resolve_cases_root(REPO_ROOT, getattr(args, 'cases_root', None))
            row = dict(summary)
            row['category'] = category_for_case_dir(case_dir, cases_root)
            row['case_dir'] = str(case_dir.resolve())
            row['exit_code'] = exit_code
            summary_doc: dict[str, Any] = {
                'mode': 'run',
                'run_date': str(args.run_date),
                'selected_count': 1,
                'batch_rollup': summarize_batch_results([row]),
                'results': [row],
            }
            written = write_summary_file(summary_path, summary_doc)
            emit_progress(f'Wrote summary: {written}')
        return exit_code
    return run_many(case_dirs, args)


def execute_run(run_dir: Path, args: argparse.Namespace, *, emit_summary: bool = True) -> tuple[int, dict[str, Any]]:
    execute_started = time.time()
    metadata = load_run_metadata(run_dir)
    case_dir = Path(metadata['case_dir'])
    case_config = load_case_config(case_dir)
    prompt = (run_dir / 'prompt.txt').read_text(encoding='utf-8')
    timeout_sec = int(getattr(args, 'openclaw_timeout', 0) or 0)
    if timeout_sec <= 0:
        timeout_sec = int(case_config.get('timeout_sec', DEFAULT_REQUEST_TIMEOUT_SEC))

    openclaw_settings = resolve_openclaw_settings(args)

    bearer_token = openclaw_settings['bearer_token']
    if not bearer_token:
        raise ValueError(
            'missing bearer token: pass --bearer-token, set OPENCLAW_BEARER_TOKEN/OPENCLAW_BENCH_TOKEN, or use OpenClaw config token discovery'
        )

    session_path = run_dir / 'session.json'
    if args.session_key:
        session_key = args.session_key
    elif session_path.exists():
        session_key = json.loads(session_path.read_text(encoding='utf-8'))['session_key']
    else:
        session_key = build_session_key(metadata['case_id'], metadata['run_id'])

    invocation = invoke_openclaw_chat(
        base_url=openclaw_settings['base_url'],
        bearer_token=bearer_token,
        model=openclaw_settings['model'],
        prompt=prompt,
        session_key=session_key,
        timeout_sec=timeout_sec,
        agent_id=openclaw_settings['agent_id'],
    )

    parsed_response = parse_json_maybe(invocation['response_text'])
    assistant_text = extract_assistant_text(parsed_response or {}) if parsed_response else ''

    write_json(run_dir / 'openclaw_request.json', invocation['request'])
    write_json(
        run_dir / 'session.json',
        {
            'session_key': session_key,
            'base_url': openclaw_settings['base_url'],
            'model': openclaw_settings['model'],
            'agent_id': openclaw_settings['agent_id'],
        },
    )

    response_record = {
        'ok': invocation['ok'],
        'status_code': invocation['status_code'],
        'duration_sec': invocation['duration_sec'],
        'headers': invocation['response_headers'],
        'json': parsed_response,
        'raw_text': invocation['response_text'],
        'error': invocation.get('error'),
        'assistant_text': assistant_text,
    }
    write_json(run_dir / 'openclaw_response.json', response_record)
    (run_dir / 'assistant.txt').write_text(assistant_text, encoding='utf-8')
    write_transcript(run_dir, prompt, assistant_text)

    trace_copy = copy_openclaw_trace(run_dir, session_key, openclaw_settings['sessions_index_path'])

    metadata = update_metadata(
        run_dir,
        status='executed',
        executed_at_epoch=time.time(),
        openclaw_base_url=openclaw_settings['base_url'],
        openclaw_model=openclaw_settings['model'],
        openclaw_agent_id=openclaw_settings['agent_id'],
        openclaw_config_path=str(openclaw_settings['openclaw_config_path']) if openclaw_settings['openclaw_config_path'] else None,
        session_key=session_key,
        request_status_code=invocation['status_code'],
        openclaw_session_id=(trace_copy or {}).get('session_id'),
        openclaw_trace_file=(trace_copy or {}).get('copied_path'),
    )

    trace_path = (trace_copy or {}).get('copied_path')
    token_usage = sum_trace_tokens(Path(trace_path) if trace_path else None)

    if not invocation['ok']:
        execute_elapsed = round(time.time() - execute_started, 3)
        metadata = update_metadata(run_dir, status='http_failed', http_error=invocation.get('error'))
        summary = {
            'case_id': metadata['case_id'],
            'run_dir': str(run_dir),
            'workspace_dir': metadata['workspace_dir'],
            'session_key': session_key,
            'http_ok': False,
            'http_status_code': invocation['status_code'],
            'error': invocation.get('error'),
            'trace_file': trace_path,
            'http_duration_sec': invocation['duration_sec'],
            'execute_duration_sec': execute_elapsed,
            'token_usage': token_usage,
        }
        if emit_summary:
            print(json.dumps(summary, indent=2))
        return 2, summary

    if parsed_response is None:
        execute_elapsed = round(time.time() - execute_started, 3)
        metadata = update_metadata(
            run_dir,
            status='protocol_failed',
            http_error='Gateway returned a non-JSON response to /v1/chat/completions',
        )
        summary = {
            'case_id': metadata['case_id'],
            'run_dir': str(run_dir),
            'workspace_dir': metadata['workspace_dir'],
            'session_key': session_key,
            'http_ok': True,
            'http_status_code': invocation['status_code'],
            'error': 'Gateway returned a non-JSON response to /v1/chat/completions',
            'trace_file': trace_path,
            'http_duration_sec': invocation['duration_sec'],
            'execute_duration_sec': execute_elapsed,
            'token_usage': token_usage,
        }
        if emit_summary:
            print(json.dumps(summary, indent=2))
        return 2, summary

    emit_progress(format_progress(str(metadata['case_id']), 'score'))
    score = evaluate_run(case_dir, run_dir)
    execute_elapsed = round(time.time() - execute_started, 3)
    update_metadata(
        run_dir,
        execute_duration_sec=execute_elapsed,
        http_duration_sec=invocation['duration_sec'],
    )
    summary = {
        'case_id': metadata['case_id'],
        'run_dir': str(run_dir),
        'workspace_dir': metadata['workspace_dir'],
        'session_key': session_key,
        'http_ok': invocation['ok'],
        'http_status_code': invocation['status_code'],
        'task_success': score['task_success'],
        'safety_success': score['safety_success'],
        'score': score['score'],
        'trace_file': trace_path,
        'http_duration_sec': invocation['duration_sec'],
        'execute_duration_sec': execute_elapsed,
        'token_usage': token_usage,
    }
    if emit_summary:
        print(json.dumps(summary, indent=2))
    return 0, summary


def score_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    metadata = load_run_metadata(run_dir)
    case_dir = Path(metadata['case_dir'])
    score = evaluate_run(case_dir, run_dir)
    print(json.dumps(score, indent=2))
    return 0


def pass_at_k_command(args: argparse.Namespace) -> int:
    """Aggregate pass@k / pass^k (hypergeometric) and discrete metrics over scored replicates.

    Each case has **n** trial directories (``--k N`` → ``run1``…``runN``, or ``--replicate``).
    Let **c** = number of trials that meet ``--metric`` (missing ``score.json`` ⇒ failure).

    **Hypergeometric estimators** (draw ``sample_k`` without replacement from the n outcomes):

    - ``pass@k`` = 1 - C(n-c, sample_k) / C(n, sample_k)
    - ``pass^k`` = C(c, sample_k) / C(n, sample_k)

    Default ``--sample-k`` equals **n** (same as number of replicates). Discrete: **≥1** success
    and **all n** successes.
    """
    run_date = str(args.run_date).strip()
    explicit = [str(x).strip() for x in (getattr(args, 'replicate', None) or []) if str(x).strip()]
    k_count = getattr(args, 'k_count', None)
    if k_count is not None and explicit:
        print('[pass-at-k] ERROR: use either --k N or --replicate RUN_NAME..., not both', file=sys.stderr)
        return 2
    if k_count is not None:
        if k_count < 1:
            print('[pass-at-k] ERROR: --k must be >= 1', file=sys.stderr)
            return 2
        replicates = [f'run{i}' for i in range(1, k_count + 1)]
    elif explicit:
        replicates = explicit
    else:
        print('[pass-at-k] ERROR: provide --k N or at least one --replicate RUN_NAME', file=sys.stderr)
        return 2

    runs_root = Path(args.runs_root).expanduser().resolve()
    partition = runs_root / run_date
    if not partition.is_dir():
        print(f'[pass-at-k] ERROR: not a directory: {partition}', file=sys.stderr)
        return 2

    metric = str(args.metric)
    threshold = float(args.score_threshold)
    sample_k_raw = getattr(args, 'sample_k', None)
    case_roots = sorted(p for p in partition.iterdir() if p.is_dir())
    model_label = getattr(args, 'model_label', None)
    try:
        doc = build_pass_metrics_document(
            run_date=run_date,
            runs_partition=partition,
            replicates=replicates,
            case_roots=case_roots,
            metric=metric,
            score_threshold=threshold,
            sample_k=int(sample_k_raw) if sample_k_raw is not None else None,
            model_label=str(model_label).strip() if model_label else None,
            source='pass_at_k_cli',
        )
    except ValueError as exc:
        print(f'[pass-at-k] ERROR: {exc}', file=sys.stderr)
        return 2

    n_cases = int(doc.get('case_count') or 0)
    n_trials = int(doc.get('n_trials') or 0)
    sample_k = int(doc.get('sample_k') or 0)
    rollup = doc.get('rollup') or {}
    pass_at_k_n = int(doc.get('pass_at_k_count') or 0)
    pass_all_k_n = int(doc.get('pass_all_k_count') or 0)
    missing_scores = int(doc.get('missing_score_files') or 0)

    summary_path = resolve_pass_at_k_summary_path(
        args, run_date=run_date, case_count=n_cases, runs_root=runs_root
    )
    if summary_path:
        written = write_summary_file(summary_path, doc)
        emit_progress(f'Wrote pass-at-k summary: {written}')

    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0

    print(
        f'runs/{run_date}/  n={n_trials}  sample_k={sample_k}  replicates={replicates!r}  metric={metric}'
        + (f'  score_threshold={threshold}' if metric == 'score' else '')
    )
    if model_label:
        print(f'model_label: {model_label}')
    print(f'cases discovered: {n_cases}')
    rh = rollup.get('mean_pass_at_k_hypergeom')
    rhp = rollup.get('mean_pass_pow_k_hypergeom')
    rh_s = f'{rh:.4f}' if rh is not None else '—'
    rhp_s = f'{rhp:.4f}' if rhp is not None else '—'
    print(f'mean pass@k (hypergeom, mean over cases): {rh_s}')
    print(f'mean pass^k (hypergeom, mean over cases): {rhp_s}')
    print(
        f'pass@k (discrete)     {pass_at_k_n}/{n_cases}  ({doc["pass_at_k_rate"]:.4f})  '
        f'[≥1 successful trial per case]'
    )
    print(
        f'pass_all_k (discrete) {pass_all_k_n}/{n_cases}  ({doc["pass_all_k_rate"]:.4f})  '
        f'[all {n_trials} trials successful; missing score.json ⇒ fail]'
    )
    if missing_scores:
        print(f'note: missing score.json entries (counted as failed trials): {missing_scores}')
    return 0
