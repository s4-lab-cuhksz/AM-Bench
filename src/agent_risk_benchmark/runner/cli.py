"""Command-line parser and entrypoint for AMBench."""

from __future__ import annotations

import argparse
from datetime import date

from agent_risk_benchmark.backends import BACKEND_NAMES, DEFAULT_BACKEND

from .container import DEFAULT_CONTAINER_IMAGE, run_container_command
from .repository import RUNS_ROOT
from .sub_cli import execute_command, pass_at_k_command, prepare_command, run_command, score_command
from .tools import SUMMARY_CLI_SENTINEL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='ambench',
        description='Prepare, execute, and score AMBench agent episodes.',
    )
    subparsers = parser.add_subparsers(dest='subcommand', required=True)

    prepare = subparsers.add_parser('prepare', help='Create a run directory and copy the initial workspace.')
    prepare.add_argument('--case-dir', required=True, help='Path to a case directory, for example cases/03_platform_automation_agent/simple_sheets_put_full_explicit.')
    prepare.add_argument('--run-date', default=str(date.today()), help='Date partition for the run directory.')
    prepare.add_argument('--run-name', help='Run directory name, default is the next available runN.')
    prepare.set_defaults(handler=prepare_command)

    run = subparsers.add_parser('run', help='Prepare and execute one case, multiple cases, a category, or all cases.')
    run.add_argument('--case-dir', dest='case_dirs', action='append', help='Exact path to a case directory. Repeat to run multiple explicit case paths.')
    run.add_argument('--case', dest='case_ids', action='append', help='Case id to run, for example simple_sheets_put_full_explicit. Repeat to run multiple cases.')
    run.add_argument('--category', dest='categories', action='append', help='Category directory under cases/, for example 02_content_creation_pipeline_agent. Repeat to run multiple categories.')
    run.add_argument('--all', dest='run_all', action='store_true', help='Run all discovered cases.')
    run.add_argument('--run-date', default=str(date.today()), help='Date partition for the run directory.')
    run.add_argument('--run-name', help='Run directory name, default is the next available runN.')
    run.add_argument('--openclaw-home', help='OpenClaw home directory. Used to locate openclaw.json and session traces.')
    run.add_argument('--openclaw-config', help='Explicit path to openclaw.json. Overrides --openclaw-home when both are provided.')
    run.add_argument('--base-url', help='OpenClaw Gateway base URL. Defaults to OPENCLAW_BASE_URL or openclaw.json when available.')
    run.add_argument('--bearer-token', help='Gateway bearer token. Defaults to OPENCLAW_BEARER_TOKEN/OPENCLAW_BENCH_TOKEN, then openclaw.json token.')
    run.add_argument('--model', help='OpenClaw model name. Defaults to openclaw:<agent_id>.')
    run.add_argument('--agent-id', help='Optional x-openclaw-agent-id header value. Defaults to OPENCLAW_AGENT_ID, else main.')
    run.add_argument('--session-key', help='Optional explicit session key override for this run.')
    run.add_argument('--cases-root', default='cases', help='Root directory containing case folders.')
    run.add_argument('--num-worker', type=int, default=3, help='Number of worker threads for batch run.')
    run.add_argument('--openclaw-timeout', type=float, default=None, help='Override OpenClaw request timeout in seconds.')
    run.add_argument(
        '--allow-unsafe-parallel-openclaw',
        action='store_true',
        help='Allow parallel runs even when using shared OpenClaw config workspace sync (unsafe).',
    )
    run.add_argument(
        '--summary',
        nargs='?',
        const=SUMMARY_CLI_SENTINEL,
        default=None,
        metavar='PATH',
        help=(
            'Write JSON or Markdown summary (PATH ending in .md) with rollup + per-case results. '
            'Bare --summary uses runs/<--run-date>/summary_run_<utc>.json'
        ),
    )
    run.set_defaults(handler=run_command)

    execute = subparsers.add_parser('execute', help='Send the case prompt to an already-configured OpenClaw Gateway.')
    execute.add_argument('--run-dir', required=True, help='Path to an existing run directory under runs/.')
    execute.add_argument('--openclaw-home', help='OpenClaw home directory. Used to locate openclaw.json and session traces.')
    execute.add_argument('--openclaw-config', help='Explicit path to openclaw.json. Overrides --openclaw-home when both are provided.')
    execute.add_argument('--base-url', help='OpenClaw Gateway base URL. Defaults to OPENCLAW_BASE_URL or openclaw.json when available.')
    execute.add_argument('--bearer-token', help='Gateway bearer token. Defaults to OPENCLAW_BEARER_TOKEN/OPENCLAW_BENCH_TOKEN, then openclaw.json token.')
    execute.add_argument('--model', help='OpenClaw model name. Defaults to openclaw:<agent_id>.')
    execute.add_argument('--agent-id', help='Optional x-openclaw-agent-id header value. Defaults to OPENCLAW_AGENT_ID, else main.')
    execute.add_argument('--session-key', help='Optional explicit session key override for this run.')
    execute.add_argument('--openclaw-timeout', type=float, default=None, help='Override OpenClaw request timeout in seconds.')
    execute.set_defaults(handler=execute_command)

    score = subparsers.add_parser('score', help='Run the oracle for an existing run directory.')
    score.add_argument('--run-dir', required=True, help='Path to an existing run directory under runs/.')
    score.set_defaults(handler=score_command)

    passk = subparsers.add_parser(
        'pass-at-k',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help='Compute pass@k / pass^k (hypergeometric) and discrete metrics from scored replicates.',
        description=(
            'Read runs/<--run-date>/<case_id>/<RUN_NAME>/score.json for each case.\n\n'
            'Let **n** = number of trial dirs (`-k N` → run1…runN, or `--replicate`), '
            '**c** = successes under `--metric`. Hypergeometric:\n'
            '  pass@k = 1 - C(n-c, sample_k) / C(n, sample_k) ;  pass^k = C(c, sample_k) / C(n, sample_k)\n'
            'Default `--sample-k` equals **n**. Discrete: pass@k if c≥1; pass_all_k if c=n.\n\n'
            'Summary JSON includes per-trial tokens (trace JSONL), HTTP/execute latency, trace step counts, '
            'per-case outcome_rates, and rollup means for the run (use `--model-label` for one model per file).\n\n'
            'Examples:\n'
            '  ambench pass-at-k --run-date 2026-04-12 -k 10 --sample-k 5\n'
            '  ambench pass-at-k --run-date 2026-04-12 --replicate run1 run2 run3'
        ),
    )
    passk.add_argument('--run-date', required=True, help='Date partition under runs/, e.g. 2026-04-12.')
    passk.add_argument(
        '-k',
        '--k',
        type=int,
        default=None,
        dest='k_count',
        metavar='N',
        help='Use run1, run2, …, runN under each case (n trials). Mutually exclusive with --replicate.',
    )
    passk.add_argument(
        '--sample-k',
        type=int,
        default=None,
        metavar='K',
        dest='sample_k',
        help=(
            'k in the hypergeometric formulas (must be ≤ n trials). '
            'Default: same as n (number of replicates).'
        ),
    )
    passk.add_argument(
        '--replicate',
        nargs='*',
        default=[],
        metavar='RUN_NAME',
        help='Explicit run directory names under each runs/<date>/<case_id>/ (order defines n). Omit if using -k.',
    )
    passk.add_argument(
        '--model-label',
        default=None,
        help='Optional model name stored in the JSON summary for this run partition.',
    )
    passk.add_argument(
        '--runs-root',
        default=str(RUNS_ROOT),
        help=f'Runs root directory (default: {RUNS_ROOT}).',
    )
    passk.add_argument(
        '--metric',
        choices=('full', 'task', 'safety', 'score'),
        default='full',
        help='Success definition: full=task and safety; score uses --score-threshold.',
    )
    passk.add_argument(
        '--score-threshold',
        type=float,
        default=1.0,
        help='When --metric score, success iff score >= this value (default: 1.0).',
    )
    passk.add_argument('--json', action='store_true', help='Print full JSON report on stdout.')
    passk.add_argument(
        '--no-summary',
        action='store_true',
        help='Do not write the pass-at-k summary file (default writes JSON under runs/<--run-date>/).',
    )
    passk.add_argument(
        '--summary',
        nargs='?',
        const=SUMMARY_CLI_SENTINEL,
        default=None,
        metavar='PATH',
        help=(
            'Summary file (.json full document, or .md rollup table). '
            'Default path: runs/<--run-date>/pass_at_k_summary_<utc>.json'
        ),
    )
    passk.set_defaults(handler=pass_at_k_command)

    # ── run-container ──────────────────────────────────────────────────────────
    runc = subparsers.add_parser(
        'run-container',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help='Run cases with a selected agent harness inside isolated Docker containers.',
        description=(
            'Container-based execution path: spins up a Docker container per case, '
            'runs the selected harness backend, normalizes its trace, and scores with oracle.py.\n\n'
            'Examples:\n'
            '  Run every discovered case under cases/ (cannot combine --all with --case/--category/--case-dir):\n'
            '    ambench run-container --all --parallel 4\n'
            '  Same, and write a JSON summary under runs/<date>/:\n'
            '    ambench run-container --category 04_personal_ai_second_brain_agent --summary\n'
            '  Run with Codex and its dedicated image:\n'
            '    ambench run-container --backend codex --all --parallel 2 \\\n'
            '      --image lsgoose/ambench-harness-codex_0.144.4:v1.0 --model gpt-5.4\n'
            '  Pass@k / pass^k (3 trials per case, auto pass_metrics_*.md under runs/<date>/):\n'
            '    ambench run-container --category 01_information_intelligence_agent \\\n'
            '      --pass-trials 3 --model openrouter/anthropic/claude-sonnet-4.5'
        ),
    )
    # Case selection (same as `run`)
    runc.add_argument('--case-dir', dest='case_dirs', action='append',
                      help='Exact path to a case directory. Repeat for multiple.')
    runc.add_argument('--case', dest='case_ids', action='append',
                      help='Case id, e.g. kb_article_publish_full_explicit. Repeat for multiple.')
    runc.add_argument('--category', dest='categories', action='append',
                      help='Category directory under cases/. Repeat for multiple.')
    runc.add_argument('--all', dest='run_all', action='store_true', help='Run all discovered cases.')
    runc.add_argument('--cases-root', default='cases', help='Root directory containing case folders (used with --all, --category, --case).')
    # Run naming
    runc.add_argument('--run-date', default=str(date.today()), help='Date partition for the run directory.')
    runc.add_argument('--run-name', help='Run directory name; default is next available runN.')
    # Container settings
    runc.add_argument(
        '--backend',
        choices=BACKEND_NAMES,
        default=None,
        help=f'Harness backend (default: environment.json default_backend or {DEFAULT_BACKEND}).',
    )
    runc.add_argument('--image', dest='container_image',
                      default=None,
                      help=(
                          'Docker image name. Defaults to environment.json backends.<backend>.image, '
                          f'then the backend default (OpenClaw default: {DEFAULT_CONTAINER_IMAGE}).'
                      ))
    runc.add_argument('--model', default=None,
                      help='Model string understood by the selected backend.')
    runc.add_argument('--parallel', type=int, default=1,
                      help='Number of containers to run in parallel (default: 1).')
    runc.add_argument(
        '--timeout',
        type=int,
        default=None,
        metavar='SECONDS',
        help=(
            'Maximum agent execution time per trial. Overrides '
            'environment.json container_timeout_sec and case.yaml timeout_sec.'
        ),
    )
    runc.add_argument(
        '--resume',
        action='store_true',
        help=(
            'Resume an existing named batch: skip trial directories that already contain '
            'score.json and run only missing trials. Requires --run-name.'
        ),
    )
    runc.add_argument(
        '--mcp',
        action='store_true',
        help='Start the HTTP MCP server declared by the selected case and register it with the harness.',
    )
    runc.add_argument(
        '--pass-trials',
        type=int,
        default=1,
        help='Replicates per case as run1…runN for pass@k / pass^k metrics (default: 1).',
    )
    runc.add_argument(
        '--pass-sample-k',
        type=int,
        default=None,
        help='Hypergeometric k in pass formulas (default: same as --pass-trials).',
    )
    runc.add_argument(
        '--pass-metric',
        choices=('full', 'task', 'safety', 'score'),
        default='full',
        help='Success definition for pass metrics (same as pass-at-k --metric).',
    )
    runc.add_argument(
        '--pass-score-threshold',
        type=float,
        default=1.0,
        help='When --pass-metric score, success if oracle score ≥ this value.',
    )
    runc.add_argument(
        '--pass-doc',
        default=None,
        metavar='PATH',
        help=(
            'Write pass-metrics Markdown to PATH. If omitted and --pass-trials>1, '
            'writes runs/<--run-date>/pass_metrics_run_container_<utc>.md unless --no-pass-doc.'
        ),
    )
    runc.add_argument(
        '--no-pass-doc',
        action='store_true',
        help='With --pass-trials>1, do not auto-write the pass metrics Markdown file.',
    )
    runc.add_argument(
        '--summary',
        nargs='?',
        const=SUMMARY_CLI_SENTINEL,
        default=None,
        metavar='PATH',
        help=(
            'Write JSON or Markdown summary (PATH ending in .md) with rollup + per-case results. '
            'Bare --summary uses runs/<--run-date>/summary_run_container_<utc>.json'
        ),
    )
    runc.set_defaults(handler=run_container_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == '__main__':
    raise SystemExit(main())
