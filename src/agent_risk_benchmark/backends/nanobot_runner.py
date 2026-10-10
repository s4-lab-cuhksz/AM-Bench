"""In-container entrypoint for one benchmark turn through the nanobot SDK."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--output', required=True)
    return parser


async def run_once(args: argparse.Namespace) -> dict[str, Any]:
    from nanobot import Nanobot

    prompt = Path(args.prompt).read_text(encoding='utf-8')
    async with Nanobot.from_config(args.config, workspace=args.workspace) as bot:
        result = await bot.run(prompt, session_key=args.session)
    return {
        'content': result.content,
        'tools_used': result.tools_used,
        'messages': result.messages,
        'usage': result.usage,
        'stop_reason': result.stop_reason,
        'error': result.error,
        'metadata': result.metadata,
    }


def main() -> int:
    args = build_parser().parse_args()
    output_path = Path(args.output)
    try:
        payload = asyncio.run(run_once(args))
    except Exception as exc:
        payload = {
            'content': '',
            'tools_used': [],
            'messages': [],
            'usage': {},
            'stop_reason': 'error',
            'error': f'{type(exc).__name__}: {exc}',
            'metadata': {},
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + '\n',
            encoding='utf-8',
        )
        raise
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + '\n',
        encoding='utf-8',
    )
    print(payload.get('content', ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
