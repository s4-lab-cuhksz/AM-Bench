"""Container-based execution path for AMBench.

Each case spins up an isolated Docker container and delegates harness-specific
configuration, execution, and trace collection to a standalone backend adapter.
The generic lifecycle stays here; harness commands live under
``agent_risk_benchmark.backends``.
"""
from __future__ import annotations

import csv
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_risk_benchmark.backends import DEFAULT_BACKEND, BackendContext, get_backend
from agent_risk_benchmark.backends.docker_runtime import DockerRuntime
from agent_risk_benchmark.backends.openclaw import DEFAULT_OPENCLAW_IMAGE

from .artifacts import materialize_run, update_metadata
from .case_mcp import case_mcp_tool_names, start_case_mcp_server
from .cases import category_for_case_dir, resolve_run_case_dirs
from .constants import DEFAULT_REQUEST_TIMEOUT_SEC
from .evaluate import evaluate_run
from .openclaw.config import load_json_dict
from .repository import REPO_ROOT, RUNS_ROOT

DEFAULT_CONTAINER_IMAGE = DEFAULT_OPENCLAW_IMAGE


def container_run_case(
    case_dir: Path,
    run_dir: Path,
    *,
    backend_name: str = DEFAULT_BACKEND,
    image: str,
    model: str | None,
    model_api_key: str = '',
    llm_params: dict[str, str] | None = None,
    backend_config: dict[str, Any] | None = None,
    mcp_enabled: bool = False,
    timeout_sec: int | None = None,
    run_idx: int,
    run_total: int,
) -> dict[str, Any]:
    """Run one case using a selected harness backend in an isolated container."""
    from .tools import (  # noqa: PLC0415
        count_model_turns,
        emit_progress,
        format_progress,
        load_case_config,
    )

    case_config = load_case_config(case_dir)
    case_id = str(case_config['case_id'])
    case_timeout_sec = int(case_config.get('timeout_sec', DEFAULT_REQUEST_TIMEOUT_SEC))
    effective_timeout_sec = timeout_sec if timeout_sec is not None else case_timeout_sec
    if effective_timeout_sec <= 0:
        raise ValueError(f'timeout must be positive, got {effective_timeout_sec}')
    prompt = (run_dir / 'prompt.txt').read_text(encoding='utf-8')
    workspace_dir = run_dir / 'workspace'

    fingerprint = hashlib.sha256(str(run_dir.resolve()).encode('utf-8')).hexdigest()[:20]
    task_id = f'risk-bench-{fingerprint}'[:63]
    backend = get_backend(backend_name)
    runtime = DockerRuntime()
    mcp_server = None
    mcp_url = None
    if mcp_enabled:
        emit_progress(format_progress(case_id, 'mcp-start', index=run_idx, total=run_total))
        mcp_server = start_case_mcp_server(case_dir, case_config, workspace_dir, run_dir)
        mcp_url = mcp_server.container_url

    context = BackendContext(
        container_name=task_id,
        case_id=case_id,
        run_dir=run_dir,
        workspace_dir=workspace_dir,
        image=image,
        model=model,
        model_api_key=model_api_key,
        llm_params=llm_params or {},
        backend_config=backend_config or {},
        prompt=prompt,
        timeout_sec=effective_timeout_sec,
        mcp_url=mcp_url,
        mcp_tool_names=case_mcp_tool_names(case_config) if mcp_enabled else (),
    )
    update_metadata(
        run_dir,
        backend=backend.name,
        container_image=image,
        model=model,
    )

    emit_progress(format_progress(case_id, 'container-start', index=run_idx, total=run_total))
    runtime.remove(task_id)
    try:
        runtime.start(
            name=task_id,
            workspace_dir=workspace_dir,
            run_dir=run_dir,
            workspace_path=backend.workspace_path,
            image=image,
            env_vars=backend.container_environment(context),
            extra_run_args=backend.container_run_args(context),
            mount_run_dir=backend.mount_run_dir(context),
        )
        emit_progress(format_progress(case_id, f'{backend.name}-prepare', index=run_idx, total=run_total))
        backend.prepare(runtime, context)
        emit_progress(format_progress(case_id, f'{backend.name}-agent', index=run_idx, total=run_total))
        execution = backend.execute(runtime, context)
    finally:
        runtime.close_process_logs()
        runtime.remove(task_id)
        if mcp_server is not None:
            mcp_server.stop()

    emit_progress(format_progress(case_id, 'score', index=run_idx, total=run_total))
    score = evaluate_run(case_dir, run_dir)
    return {
        'case_id': case_id,
        'run_dir': str(run_dir),
        'workspace_dir': str(workspace_dir),
        'task_success': score.get('task_success', False),
        'safety_success': score.get('safety_success', False),
        'task_score': score.get('task_score'),
        'process_score': score.get('process_score', score.get('task_score')),
        'tool_efficiency_score': score.get('tool_efficiency_score'),
        'safety_score': score.get('safety_score'),
        'clarification_attempted': score.get('clarification_attempted'),
        'clarification_quality': score.get('clarification_quality'),
        'blocked_action': score.get('blocked_action'),
        'redundant_tool_call_count': score.get('redundant_tool_call_count'),
        'tool_call_average': score.get('tool_call_average'),
        'redundant_tool_call_average': score.get('redundant_tool_call_average'),
        'redundant_tool_call_flags': score.get('redundant_tool_call_flags'),
        'score': score.get('score', 0.0),
        'token_usage': execution.token_usage,
        'model_turn_count': count_model_turns(run_dir / 'bench-run.jsonl'),
        'execute_duration_sec': execution.elapsed_sec,
        'container_image': image,
        'backend': backend.name,
        'timed_out': execution.timed_out,
    }


# ── Batch container command ────────────────────────────────────────────────────

def load_local_environment() -> dict[str, Any]:
    """Load optional environment.json from the benchmark repo root."""
    env_path = REPO_ROOT / 'environment.json'
    if not env_path.exists():
        return {}
    return load_json_dict(env_path, label='environment.json')


def _llm_params_from_local_env(local_env: dict[str, Any]) -> dict[str, str]:
    """Read ``temperature`` / ``topP`` / ``maxTokens`` from environment.json.

    Preferred: a ``params`` object (same shape as openclaw ``agents.defaults.models[primary].params``).
    Legacy: root-level ``temperature`` (and optional ``topP``, ``maxTokens``) for older configs.
    """
    keys = ('temperature', 'topP', 'maxTokens')
    merged: dict[str, str] = {}
    params_block = local_env.get('params')
    if isinstance(params_block, dict):
        for k in keys:
            v = params_block.get(k)
            if v is not None and str(v).strip() != '':
                merged[k] = str(v).strip()
    for k in keys:
        if k in merged:
            continue
        v = local_env.get(k)
        if v is not None and str(v).strip() != '':
            merged[k] = str(v).strip()
    return merged


def _resolve_container_args(args: Any, local_env: dict[str, Any]) -> dict[str, Any]:
    """Resolve backend-specific image, model, credentials, and parallelism."""
    backend_name = str(
        getattr(args, 'backend', None)
        or local_env.get('default_backend')
        or local_env.get('backend')
        or DEFAULT_BACKEND
    ).strip().lower()
    backend = get_backend(backend_name)

    backends_config = local_env.get('backends')
    backend_config: dict[str, Any] = {}
    if isinstance(backends_config, dict):
        telemetry_base_name = {
            'nanobot-telemetry': 'nanobot',
            'openclaw-telemetry': 'openclaw',
        }.get(backend_name)
        if telemetry_base_name:
            base_candidate = backends_config.get(telemetry_base_name)
            if isinstance(base_candidate, dict):
                # Reuse provider/model credentials without accidentally
                # selecting an image that lacks the telemetry collector.
                backend_config.update(
                    {
                        key: value
                        for key, value in base_candidate.items()
                        if key != 'image'
                    }
                )
        candidate = backends_config.get(backend_name)
        if isinstance(candidate, dict):
            backend_config.update(candidate)

    legacy_image = local_env.get('container_image') if backend_name == DEFAULT_BACKEND else None
    image = (
        getattr(args, 'container_image', None)
        or backend_config.get('image')
        or legacy_image
        or backend.default_image
    )
    cli_model = getattr(args, 'model', None)
    model = (
        cli_model
        or backend_config.get('model')
        or local_env.get('model')
        or local_env.get('default_model')
        or None
    )
    if cli_model and backend_name in {'openclaw', 'openclaw-telemetry', 'codex'}:
        provider = str(backend_config.get('provider', '')).strip()
        model_id = str(cli_model)
        if provider and model_id.startswith(f'{provider}/'):
            model_id = model_id[len(provider) + 1 :]
        backend_config['model_id'] = model_id
    parallel = max(1, int(getattr(args, 'parallel', 1) or 1))
    configured_timeout = getattr(args, 'timeout', None)
    if configured_timeout is None:
        configured_timeout = local_env.get('container_timeout_sec')
    timeout_sec = int(configured_timeout) if configured_timeout is not None else None
    if timeout_sec is not None and timeout_sec <= 0:
        raise ValueError(f'container timeout must be positive, got {timeout_sec}')

    configured_key = backend_config.get('model_api_key') or local_env.get('model_api_key', '')
    api_key_env = str(backend_config.get('api_key_env', '')).strip()
    provider_name = str(backend_config.get('provider', '')).strip()
    if not provider_name and model and '/' in str(model):
        provider_name = str(model).split('/', 1)[0]
    default_api_key_env = {
        'deepseek': 'DEEPSEEK_API_KEY',
        'anthropic': 'ANTHROPIC_API_KEY',
        'openai': 'OPENAI_API_KEY',
        'openrouter': 'OPENROUTER_API_KEY',
        'google': 'GOOGLE_API_KEY',
        'gemini': 'GEMINI_API_KEY',
        'moonshot': 'MOONSHOT_API_KEY',
    }.get(provider_name, '')
    selected_key_env = api_key_env or default_api_key_env
    selected_environment_key = os.environ.get(selected_key_env, '') if selected_key_env else ''
    if backend_name == 'codex':
        environment_key = (
            selected_environment_key
            or os.environ.get('CODEX_API_KEY', '')
            or os.environ.get('OPENAI_API_KEY', '')
            or os.environ.get('OPENROUTER_API_KEY', '')
            or os.environ.get('MODEL_API_KEY', '')
        )
    elif backend_name == 'claude':
        environment_key = (
            os.environ.get('ANTHROPIC_AUTH_TOKEN', '')
            or os.environ.get('ANTHROPIC_API_KEY', '')
            or os.environ.get('MODEL_API_KEY', '')
        )
    else:
        environment_key = selected_environment_key or os.environ.get('MODEL_API_KEY', '')

    return {
        'backend': backend_name,
        'image': str(image),
        'model': str(model) if model is not None else None,
        'parallel': parallel,
        'timeout_sec': timeout_sec,
        'model_api_key': str(configured_key or environment_key),
        'llm_params': _llm_params_from_local_env({**local_env, **backend_config}),
        'backend_config': dict(backend_config),
        'mcp_enabled': bool(getattr(args, 'mcp', False)),
    }


_PER_CASE_MODEL_TSV_FIELDNAMES: tuple[str, ...] = (
    'backend',
    'model',
    'written_at_utc',
    'pass_trials',
    'sample_k',
    'pass_metric',
    'pass_score_threshold',
    'c_success',
    'n_trials',
    'pass_at_k_hypergeom',
    'pass_pow_k_hypergeom',
    'discrete_pass_at_k',
    'discrete_pass_pow_k',
    'discrete_pass_all_k',
    'mean_task_success_rate',
    'mean_safety_success_rate',
    'mean_score',
    'task_progress',
    'tok_in_sum',
    'tok_out_sum',
    'cache_read_sum',
    'cache_write_sum',
    'tok_total_sum',
    'mean_http_duration_sec',
    'mean_execute_duration_sec',
    'model_turns_sum',
    'trace_steps_sum',
    'last_trial_run_dir',
    'batch_summary_json',
)


def _discrete_pass_pow_k_case_rate(pm: dict[str, Any]) -> float | None:
    """Fraction of cases with c_success >= sample_k (discrete counterpart to pass^k)."""
    pcs = pm.get('per_case') or []
    try:
        sk = int(pm.get('sample_k') or 0)
    except (TypeError, ValueError):
        sk = 0
    if not pcs or sk < 1:
        return None
    ok = sum(1 for pc in pcs if int(pc.get('c_success') or 0) >= sk)
    return round(ok / len(pcs), 6)


def _fmt_cell(v: Any, *, nd: int = 6) -> str:
    if v is None:
        return ''
    if isinstance(v, bool):
        return '1' if v else '0'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = f'{v:.{nd}f}'.rstrip('0').rstrip('.')
        return s if s not in ('-0', '') else '0'
    return str(v)


def _write_per_case_model_comparison_tsv(
    *,
    runs_date_dir: Path,
    repo_root: Path,
    backend: str,
    model: str | None,
    pass_trials: int,
    pass_metric: str,
    pass_score_threshold: float,
    sample_k: int | None,
    pm_doc: dict[str, Any] | None,
    case_run_paths: dict[str, list[Path]],
    summary_path: Path | None,
    emit_progress: Any,
) -> None:
    """Append/update one row per model under each case dir for cross-model comparison tables."""
    from .tools import (  # noqa: PLC0415
        collect_trial_run_metrics,
        load_score_json,
        mean_reasoning_progress_ratio_from_score_dicts,
        trial_satisfies_metric,
    )

    backend_key = backend.strip() or DEFAULT_BACKEND
    model_key = (model or '').strip() or 'unknown'
    comparison_key = f'{backend_key}:{model_key}'
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    summary_cell = ''
    if summary_path is not None:
        try:
            summary_cell = str(summary_path.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            summary_cell = str(summary_path.resolve())

    def _row_from_per_case(pc: dict[str, Any], paths: list[Path]) -> dict[str, str]:
        rates = pc.get('outcome_rates') or {}
        agg = pc.get('aggregates') or {}
        tu = agg.get('token_usage_sum') or {}
        last_dir = str(paths[-1].resolve()) if paths else ''
        c_succ = int(pc.get('c_success') or 0)
        try:
            sk_pc = int(pc.get('sample_k') or 0)
        except (TypeError, ValueError):
            sk_pc = 0
        disc_pow = sk_pc > 0 and c_succ >= sk_pc
        return {
            'backend': backend_key,
            'model': model_key,
            'written_at_utc': ts,
            'pass_trials': str(pass_trials),
            'sample_k': '' if sample_k is None else str(sample_k),
            'pass_metric': pass_metric,
            'pass_score_threshold': _fmt_cell(pass_score_threshold, nd=4),
            'c_success': str(c_succ),
            'n_trials': str(int(pc.get('n_trials') or 0)),
            'pass_at_k_hypergeom': _fmt_cell(pc.get('pass_at_k_hypergeom'), nd=8),
            'pass_pow_k_hypergeom': _fmt_cell(pc.get('pass_pow_k_hypergeom'), nd=8),
            'discrete_pass_at_k': _fmt_cell(bool(pc.get('pass_at_k'))),
            'discrete_pass_pow_k': _fmt_cell(disc_pow),
            'discrete_pass_all_k': _fmt_cell(bool(pc.get('pass_all_k'))),
            'mean_task_success_rate': _fmt_cell(rates.get('mean_task_success_rate'), nd=6),
            'mean_safety_success_rate': _fmt_cell(rates.get('mean_safety_success_rate'), nd=6),
            'mean_score': _fmt_cell(rates.get('mean_score'), nd=6),
            'task_progress': _fmt_cell(rates.get('task_progress'), nd=6),
            'tok_in_sum': str(int(tu.get('input', 0) or 0)),
            'tok_out_sum': str(int(tu.get('output', 0) or 0)),
            'cache_read_sum': str(int(tu.get('cache_read', 0) or 0)),
            'cache_write_sum': str(int(tu.get('cache_write', 0) or 0)),
            'tok_total_sum': str(int(tu.get('total', 0) or 0)),
            'mean_http_duration_sec': _fmt_cell(agg.get('mean_http_duration_sec'), nd=6),
            'mean_execute_duration_sec': _fmt_cell(agg.get('mean_execute_duration_sec'), nd=6),
            'model_turns_sum': str(int(agg.get('model_turn_count_sum') or 0)),
            'trace_steps_sum': str(int(agg.get('trace_step_count_sum') or 0)),
            'last_trial_run_dir': last_dir,
            'batch_summary_json': summary_cell,
        }

    def _row_fallback(_case_id: str, paths: list[Path]) -> dict[str, str]:
        n = len(paths)
        c_ok = 0
        tok_in = tok_out = cread = cwr = ttot = 0
        steps = 0
        turns = 0
        https: list[float] = []
        execs: list[float] = []
        scores: list[float] = []
        score_dicts_for_prog: list[dict[str, Any]] = []
        tasks = 0
        safes = 0
        scored = 0
        last_dir = ''
        for rd in paths:
            if not rd.is_dir():
                continue
            last_dir = str(rd.resolve())
            sc = load_score_json(rd)
            m = collect_trial_run_metrics(rd)
            tu = m.get('token_usage') or {}
            tok_in += int(tu.get('input', 0) or 0)
            tok_out += int(tu.get('output', 0) or 0)
            cread += int(tu.get('cache_read', 0) or 0)
            cwr += int(tu.get('cache_write', 0) or 0)
            ttot += int(tu.get('total', 0) or 0)
            steps += int(m.get('trace_step_count') or 0)
            turns += int(m.get('model_turn_count') or 0)
            if m.get('http_duration_sec') is not None:
                https.append(float(m['http_duration_sec']))
            if m.get('execute_duration_sec') is not None:
                execs.append(float(m['execute_duration_sec']))
            if sc is not None:
                scored += 1
                score_dicts_for_prog.append(sc)
                if trial_satisfies_metric(sc, pass_metric, pass_score_threshold):
                    c_ok += 1
                if sc.get('task_success'):
                    tasks += 1
                if sc.get('safety_success'):
                    safes += 1
                try:
                    scores.append(float(sc.get('score', 0) or 0))
                except (TypeError, ValueError):
                    pass
        mean_sc = sum(scores) / len(scores) if scores else None
        mt = tasks / scored if scored else None
        ms = safes / scored if scored else None
        rp = mean_reasoning_progress_ratio_from_score_dicts(score_dicts_for_prog)
        prog = rp if rp is not None else (mean_sc if mean_sc is not None else mt)
        m_http = round(sum(https) / len(https), 6) if https else None
        m_ex = round(sum(execs) / len(execs), 6) if execs else None
        sk_eff = int(sample_k) if sample_k is not None else n
        disc_pow = n > 0 and sk_eff > 0 and c_ok >= sk_eff
        return {
            'backend': backend_key,
            'model': model_key,
            'written_at_utc': ts,
            'pass_trials': str(pass_trials),
            'sample_k': '' if sample_k is None else str(sample_k),
            'pass_metric': pass_metric,
            'pass_score_threshold': _fmt_cell(pass_score_threshold, nd=4),
            'c_success': str(c_ok),
            'n_trials': str(n),
            'pass_at_k_hypergeom': '',
            'pass_pow_k_hypergeom': '',
            'discrete_pass_at_k': _fmt_cell(c_ok >= 1) if n else '',
            'discrete_pass_pow_k': _fmt_cell(disc_pow) if n else '',
            'discrete_pass_all_k': _fmt_cell(c_ok == n) if n else '',
            'mean_task_success_rate': _fmt_cell(mt, nd=6),
            'mean_safety_success_rate': _fmt_cell(ms, nd=6),
            'mean_score': _fmt_cell(mean_sc, nd=6) if mean_sc is not None else '',
            'task_progress': _fmt_cell(prog, nd=6) if prog is not None else '',
            'tok_in_sum': str(tok_in),
            'tok_out_sum': str(tok_out),
            'cache_read_sum': str(cread),
            'cache_write_sum': str(cwr),
            'tok_total_sum': str(ttot),
            'mean_http_duration_sec': _fmt_cell(m_http, nd=6),
            'mean_execute_duration_sec': _fmt_cell(m_ex, nd=6),
            'model_turns_sum': str(turns),
            'trace_steps_sum': str(steps),
            'last_trial_run_dir': last_dir,
            'batch_summary_json': summary_cell,
        }

    per_by_id: dict[str, dict[str, Any]] = {}
    if pm_doc:
        for pc in pm_doc.get('per_case') or []:
            cid = str(pc.get('case_id') or '').strip()
            if cid:
                per_by_id[cid] = pc

    for case_id, paths in sorted(case_run_paths.items(), key=lambda x: x[0]):
        case_dir = runs_date_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        out_path = case_dir / 'run_container_models.tsv'
        rows_by_model: dict[str, dict[str, str]] = {}
        if out_path.is_file():
            with out_path.open(newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for r in reader:
                    if not r:
                        continue
                    mk = (r.get('model') or '').strip()
                    bk = (r.get('backend') or DEFAULT_BACKEND).strip()
                    if mk:
                        rows_by_model[f'{bk}:{mk}'] = {k: (r.get(k) or '') for k in _PER_CASE_MODEL_TSV_FIELDNAMES}

        pc = per_by_id.get(case_id)
        if pc is not None:
            new_row = _row_from_per_case(pc, paths)
        else:
            new_row = _row_fallback(case_id, paths)
        rows_by_model[comparison_key] = new_row

        with out_path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=_PER_CASE_MODEL_TSV_FIELDNAMES, delimiter='\t', extrasaction='ignore')
            writer.writeheader()
            for mk in sorted(rows_by_model):
                writer.writerow({k: rows_by_model[mk].get(k, '') for k in _PER_CASE_MODEL_TSV_FIELDNAMES})
    emit_progress(
        f'[run-container] per-case backend/model tables: {len(case_run_paths)} × run_container_models.tsv '
        f'under {runs_date_dir}/<case_id>/'
    )


def run_container_command(args: Any) -> int:
    """CLI handler for the ``run-container`` subcommand."""
    from .tools import (  # noqa: PLC0415
        build_pass_metrics_document,
        collect_trial_run_metrics,
        emit_progress,
        format_batch_rollup_text,
        format_progress,
        load_case_config,
        load_score_json,
        resolve_cases_root,
        resolve_summary_outpath,
        summarize_batch_results,
        write_summary_file,
    )

    case_dirs = resolve_run_case_dirs(args)
    cases_root = resolve_cases_root(REPO_ROOT, getattr(args, 'cases_root', None))
    local_env = load_local_environment()
    ctr = _resolve_container_args(args, local_env)
    run_date = args.run_date
    total = len(case_dirs)

    emit_progress(
        f'run-container: {total} case(s), backend={ctr["backend"]}, image={ctr["image"]}, '
        f'model={ctr["model"] or "backend-default"}, parallel={ctr["parallel"]}, '
        f'timeout={ctr["timeout_sec"] or "case-default"}s'
    )

    results: list[dict[str, Any]] = []
    batch_started = time.time()
    overall_exit = 0
    pass_trials = max(1, int(getattr(args, 'pass_trials', 1) or 1))
    resume = bool(getattr(args, 'resume', False))
    if resume and not getattr(args, 'run_name', None):
        raise ValueError('--resume requires --run-name')

    # On Ctrl-C / SIGTERM, clean up any leftover risk-bench-* containers
    def _cleanup_on_exit(signum, frame):  # noqa: ANN001
        emit_progress('\n[run-container] interrupted — cleaning up containers...')
        subprocess.run(
            'docker ps -a --filter "name=risk-bench-" -q | xargs -r docker rm -f',
            shell=True, capture_output=True,
        )
        sys.exit(1)

    signal.signal(signal.SIGINT, _cleanup_on_exit)
    signal.signal(signal.SIGTERM, _cleanup_on_exit)

    def _run_one(idx_case: tuple[int, Path]) -> dict[str, Any]:
        idx, case_dir = idx_case
        cfg = load_case_config(case_dir)
        case_id_key = str(cfg['case_id'])
        emit_progress(format_progress(case_id_key, 'prepare', index=idx, total=total))
        trial_paths: list[Path] = []
        trial_results: list[dict[str, Any]] = []

        def _existing_result(run_dir: Path, score: dict[str, Any]) -> dict[str, Any]:
            metrics = collect_trial_run_metrics(run_dir)
            return {
                'case_id': case_id_key,
                'run_dir': str(run_dir),
                'workspace_dir': str(run_dir / 'workspace'),
                'task_success': score.get('task_success', False),
                'safety_success': score.get('safety_success', False),
                'task_score': score.get('task_score'),
                'process_score': score.get('process_score', score.get('task_score')),
                'tool_efficiency_score': score.get('tool_efficiency_score'),
                'safety_score': score.get('safety_score'),
                'clarification_attempted': score.get('clarification_attempted'),
                'clarification_quality': score.get('clarification_quality'),
                'blocked_action': score.get('blocked_action'),
                'redundant_tool_call_count': score.get('redundant_tool_call_count'),
                'tool_call_average': score.get('tool_call_average'),
                'redundant_tool_call_average': score.get('redundant_tool_call_average'),
                'redundant_tool_call_flags': score.get('redundant_tool_call_flags'),
                'score': score.get('score', 0.0),
                'token_usage': metrics.get('token_usage') or {},
                'model_turn_count': metrics.get('model_turn_count', 0),
                'execute_duration_sec': metrics.get('execute_duration_sec') or 0.0,
                'container_image': ctr['image'],
                'backend': ctr['backend'],
                'timed_out': False,
                'resumed_existing': True,
            }

        def _execute_trial(slot: str) -> tuple[Path, dict[str, Any]]:
            expected_dir = RUNS_ROOT / str(run_date) / case_id_key / slot
            existing_score = load_score_json(expected_dir) if resume else None
            if existing_score is not None:
                emit_progress(
                    format_progress(case_id_key, f'resume-skip {slot}', index=idx, total=total)
                )
                return expected_dir, _existing_result(expected_dir, existing_score)

            run_dir = expected_dir
            try:
                if resume and expected_dir.exists():
                    emit_progress(
                        format_progress(
                            case_id_key,
                            f'resume-reset {slot}',
                            index=idx,
                            total=total,
                        )
                    )
                    shutil.rmtree(expected_dir)
                _cc, run_dir = materialize_run(case_dir, run_date, slot)
                result = container_run_case(
                    case_dir,
                    run_dir,
                    backend_name=ctr['backend'],
                    image=ctr['image'],
                    model=ctr['model'],
                    model_api_key=ctr.get('model_api_key', ''),
                    llm_params=ctr.get('llm_params'),
                    backend_config=ctr.get('backend_config'),
                    mcp_enabled=ctr.get('mcp_enabled', False),
                    timeout_sec=ctr.get('timeout_sec'),
                    run_idx=idx,
                    run_total=total,
                )
            except Exception as exc:  # Keep the batch resumable when one harness trial fails.
                result = {
                    'case_id': case_id_key,
                    'run_dir': str(run_dir),
                    'workspace_dir': str(run_dir / 'workspace'),
                    'error': f'{type(exc).__name__}: {exc}'[-4000:],
                    'token_usage': {},
                    'model_turn_count': 0,
                    'execute_duration_sec': 0.0,
                    'container_image': ctr['image'],
                    'backend': ctr['backend'],
                    'timed_out': False,
                }
                emit_progress(
                    format_progress(case_id_key, f'error {slot}: {type(exc).__name__}', index=idx, total=total)
                )
            return run_dir, result

        if pass_trials > 1:
            # Name trials so different --run-name batches do not reuse run1..runN (e.g. model matrix on same date).
            batch_tag = getattr(args, 'run_name', None)
            for tr in range(1, pass_trials + 1):
                slot = f'{batch_tag}_run{tr}' if batch_tag else f'run{tr}'
                run_dir, result = _execute_trial(slot)
                trial_paths.append(Path(result['run_dir']))
                trial_results.append(result)
        else:
            run_name = getattr(args, 'run_name', None)
            single_slot = str(run_name) if run_name is not None else None
            if single_slot is None:
                _cc, run_dir = materialize_run(case_dir, run_date, None)
                # The directory is already materialized; execute it directly.
                try:
                    result = container_run_case(
                        case_dir, run_dir,
                        backend_name=ctr['backend'], image=ctr['image'], model=ctr['model'],
                        model_api_key=ctr.get('model_api_key', ''), llm_params=ctr.get('llm_params'),
                        backend_config=ctr.get('backend_config'), mcp_enabled=ctr.get('mcp_enabled', False),
                        timeout_sec=ctr.get('timeout_sec'), run_idx=idx, run_total=total,
                    )
                except Exception as exc:
                    result = {
                        'case_id': case_id_key, 'run_dir': str(run_dir),
                        'workspace_dir': str(run_dir / 'workspace'),
                        'error': f'{type(exc).__name__}: {exc}'[-4000:], 'token_usage': {},
                        'model_turn_count': 0, 'execute_duration_sec': 0.0,
                        'container_image': ctr['image'], 'backend': ctr['backend'], 'timed_out': False,
                    }
            else:
                run_dir, result = _execute_trial(single_slot)
            trial_paths.append(Path(result['run_dir']))
            trial_results.append(result)
        for r in trial_results:
            r['category'] = category_for_case_dir(case_dir, cases_root)
            r['case_dir'] = str(case_dir.resolve())
        last = trial_results[-1]
        emit_progress(
            format_progress(
                str(last.get('case_id', case_id_key)),
                f'complete ({len(trial_results)} trial(s); last score={last.get("score")})',
                index=idx,
                total=total,
            )
        )
        return {'case_id': case_id_key, 'trial_paths': trial_paths, 'trial_results': trial_results}

    indexed = list(enumerate(case_dirs, start=1))
    blobs: list[dict[str, Any]] = []
    if ctr['parallel'] == 1:
        blobs = [_run_one(ic) for ic in indexed]
    else:
        with ThreadPoolExecutor(max_workers=ctr['parallel']) as pool:
            futures = {pool.submit(_run_one, ic): ic for ic in indexed}
            for fut in as_completed(futures):
                blobs.append(fut.result())

    case_run_paths: dict[str, list[Path]] = {}
    for b in blobs:
        case_run_paths[b['case_id']] = b['trial_paths']
        results.extend(b['trial_results'])

    batch_elapsed = round(time.time() - batch_started, 3)

    # ── Per-case table ─────────────────────────────────────────────────────────
    col_w = max(len(r.get('case_id', '')) for r in results)
    header = (
        f"{'case':<{col_w}}  task_ok safe_ok clarify clar_q blocked tool_avg  proc_sc eff_sc safe_sc score   "
        f"tok_in  tok_out  tok_total  turns  secs"
    )
    print(header)
    print('-' * len(header))
    for r in results:
        tu = r.get('token_usage') or {}
        if 'error' in r:
            print(f"{r.get('case_id', '?'):<{col_w}}  ERROR: {r['error']}")
        else:
            task   = '✓' if r.get('task_success')   else '✗'
            safety = '✓' if r.get('safety_success')  else '✗'
            clarification = r.get('clarification_attempted')
            clarify = '-' if clarification is None else ('✓' if clarification else '✗')
            clarify_quality = r.get('clarification_quality')
            clarify_quality_s = '-' if clarify_quality is None else f'{float(clarify_quality):.2f}'
            blocked_action = r.get('blocked_action')
            blocked = '-' if blocked_action is None else ('✓' if blocked_action else '✗')
            redundant_average = r.get('tool_call_average')
            if redundant_average is None:
                redundant_average = r.get('redundant_tool_call_average')
            redundant_count = r.get('redundant_tool_call_count')
            if redundant_average is not None:
                redundant = f'{float(redundant_average):.2f}'
            elif redundant_count is not None:
                redundant = f'{float(redundant_count):.2f}'
            else:
                redundant = '-'
            process_score = r.get('process_score')
            if process_score is None:
                process_score = r.get('task_score')
            if process_score is None:
                process_score = 1.0 if r.get('task_success') else 0.0
            efficiency_score = r.get('tool_efficiency_score')
            efficiency_score_s = '-' if efficiency_score is None else f'{float(efficiency_score):.2f}'
            safety_score = r.get('safety_score')
            if safety_score is None:
                safety_score = 1.0 if r.get('safety_success') else 0.0
            print(
                f"{r['case_id']:<{col_w}}  {task:<7} {safety:<7} {clarify:<7} {clarify_quality_s:>6} {blocked:<7} {redundant:>9}  "
                f"{float(process_score):>7.2f} {efficiency_score_s:>6} {float(safety_score):>7.2f} "
                f"{r.get('score', 0.0):.2f}   "
                f"{tu.get('input', 0):>6}  {tu.get('output', 0):>7}  "
                f"{tu.get('total', 0):>9}  {r.get('model_turn_count', 0):>5}  "
                f"{r.get('execute_duration_sec', 0):.1f}s"
            )

    # ── Batch summary (by category + overall) ──────────────────────────────────
    rollups = summarize_batch_results(results)

    pm_doc: dict[str, Any] | None = None
    try:
        pm_doc = build_pass_metrics_document(
            run_date=str(run_date),
            runs_partition=RUNS_ROOT / str(run_date),
            case_run_paths=case_run_paths,
            metric=str(getattr(args, 'pass_metric', 'full')),
            score_threshold=float(getattr(args, 'pass_score_threshold', 1.0)),
            sample_k=getattr(args, 'pass_sample_k', None),
            model_label=(
                f'{ctr["backend"]}:{ctr["model"]}' if ctr.get('model') else ctr['backend']
            ),
            source='run_container',
        )
    except ValueError as exc:
        emit_progress(f'[run-container] pass metrics skipped: {exc}')

    batch_doc: dict[str, Any] = {
        'mode': 'run_container',
        'run_date': run_date,
        'cases_root': str(cases_root),
        'backend': ctr['backend'],
        'container_image': ctr['image'],
        'model': ctr['model'],
        'parallel': ctr['parallel'],
        'timeout_sec': ctr['timeout_sec'],
        'pass_trials': pass_trials,
        'wall_clock_sec': batch_elapsed,
        'batch_rollup': rollups,
        'results': results,
    }
    if pm_doc is not None:
        batch_doc['pass_metrics'] = pm_doc
        roll = pm_doc.get('rollup') or {}
        tok = roll.get('total_token_usage') or {}
        disc_pow_k_rate = _discrete_pass_pow_k_case_rate(pm_doc)
        batch_doc['summary_table_row'] = {
            'backend': ctr.get('backend'),
            'model': ctr.get('model'),
            'run_date': str(run_date),
            'pass_trials': pass_trials,
            'sample_k': pm_doc.get('sample_k'),
            'pass_at_k': roll.get('mean_pass_at_k_hypergeom'),
            'pass_pow_k': roll.get('mean_pass_pow_k_hypergeom'),
            'hypergeom_mean_pass_at_k': roll.get('mean_pass_at_k_hypergeom'),
            'hypergeom_mean_pass_pow_k': roll.get('mean_pass_pow_k_hypergeom'),
            'discrete_pass_at_k_case_rate': pm_doc.get('pass_at_k_rate'),
            'discrete_pass_pow_k_case_rate': disc_pow_k_rate,
            'discrete_pass_all_n_case_rate': pm_doc.get('pass_all_k_rate'),
            'token_input': int(tok.get('input', 0) or 0),
            'token_output': int(tok.get('output', 0) or 0),
            'token_cache_read': int(tok.get('cache_read', 0) or 0),
            'token_cache_write': int(tok.get('cache_write', 0) or 0),
            'token_total': int(tok.get('total', 0) or 0),
            'mean_http_duration_sec': roll.get('mean_http_duration_sec'),
            'mean_execute_duration_sec': roll.get('mean_execute_duration_sec'),
            'total_model_turns': int(roll.get('total_model_turns', 0) or 0),
            'mean_task_progress': roll.get('mean_task_progress'),
            'trials_with_http_latency': int(roll.get('trials_with_http_latency', 0) or 0),
            'trials_with_execute_duration': int(roll.get('trials_with_execute_duration', 0) or 0),
            'total_trace_steps': int(roll.get('total_trace_steps', 0) or 0),
        }

    pass_doc_path = getattr(args, 'pass_doc', None)
    no_pass_doc = bool(getattr(args, 'no_pass_doc', False))
    if pm_doc and int(pm_doc.get('case_count') or 0) > 0:
        write_pass_md = False
        target: Path | None = None
        if pass_doc_path:
            target = Path(str(pass_doc_path)).expanduser().resolve()
            write_pass_md = True
        elif pass_trials > 1 and not no_pass_doc:
            ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            target = (RUNS_ROOT / str(run_date) / f'pass_metrics_run_container_{ts}.md').resolve()
            write_pass_md = True
        if write_pass_md and target is not None:
            written_pass = write_summary_file(target, pm_doc)
            emit_progress(f'Wrote pass metrics document: {written_pass}')
    summary_path = resolve_summary_outpath(
        args,
        run_date=str(run_date),
        mode_label='run-container',
        case_count=total,
        runs_root=RUNS_ROOT,
    )
    written_summary_path: Path | None = None
    if summary_path:
        written_summary_path = write_summary_file(summary_path, batch_doc)
        emit_progress(f'Wrote summary: {written_summary_path}')

    sample_k_val: int | None = None
    if pm_doc is not None and pm_doc.get('sample_k') is not None:
        sample_k_val = int(pm_doc['sample_k'])
    else:
        raw_sk = getattr(args, 'pass_sample_k', None)
        if raw_sk is not None:
            sample_k_val = int(raw_sk)

    if case_run_paths:
        _write_per_case_model_comparison_tsv(
            runs_date_dir=RUNS_ROOT / str(run_date),
            repo_root=REPO_ROOT,
            backend=ctr.get('backend', DEFAULT_BACKEND),
            model=ctr.get('model'),
            pass_trials=pass_trials,
            pass_metric=str(getattr(args, 'pass_metric', 'full')),
            pass_score_threshold=float(getattr(args, 'pass_score_threshold', 1.0)),
            sample_k=sample_k_val,
            pm_doc=pm_doc,
            case_run_paths=case_run_paths,
            summary_path=written_summary_path,
            emit_progress=emit_progress,
        )

    print()
    print(
        f"backend={ctr['backend']}  image={ctr['image']}  "
        f"model={ctr['model'] or 'backend-default'}  wall_clock_sec={batch_elapsed}"
    )
    print(format_batch_rollup_text(rollups))
    if pm_doc and pm_doc.get('rollup'):
        pr = pm_doc['rollup']
        m1 = pr.get('mean_pass_at_k_hypergeom')
        m2 = pr.get('mean_pass_pow_k_hypergeom')
        m1s = f'{m1:.4f}' if m1 is not None else '—'
        m2s = f'{m2:.4f}' if m2 is not None else '—'
        print(
            f'pass_metrics (hypergeom, mean over cases): pass@k={m1s}  pass^k={m2s}  '
            f'(pass_trials={pass_trials}, sample_k={pm_doc.get("sample_k")})'
        )

    errors = rollups['overall']['error_count']
    if errors:
        overall_exit = 1
    return overall_exit
