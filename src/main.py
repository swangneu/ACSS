from __future__ import annotations

import argparse
from pathlib import Path

from src.orchestrator import ACSSOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description='ACSS Agentic AI runner')
    parser.add_argument('--requirements', type=Path, required=True, help='Path to requirements JSON')
    parser.add_argument('--out', type=Path, default=Path('runs'), help='Output directory root')
    parser.add_argument(
        '--template-slx',
        type=Path,
        default=None,
        help='Path to Simulink template (.slx). If omitted, the AI generates the model from scratch.',
    )
    parser.add_argument(
        '--human-review',
        action='store_true',
        help='Pause after each workflow step and allow manual approval or JSON edits',
    )
    parser.add_argument(
        '--workflow-mode',
        choices=['legacy', 'layered'],
        default='legacy',
        help='Workflow mode. legacy preserves old loop, layered enables architecture-aware diagnosis/decisions.',
    )
    parser.add_argument(
        '--matlab-backend',
        choices=['mcp', 'batch', 'auto'],
        default='auto',
        help='MATLAB execution backend. "mcp" uses a persistent MCP server (requires mcp + matlab-mcp-core-server). '
             '"batch" spawns matlab -batch each time. "auto" tries MCP first, falls back to batch.',
    )
    args = parser.parse_args()

    orch = ACSSOrchestrator(
        args.requirements,
        args.out,
        template_slx=args.template_slx,
        human_review=args.human_review,
        workflow_mode=args.workflow_mode,
        matlab_backend=args.matlab_backend,
    )
    run_dir = orch.run()
    print(f'Run complete: {run_dir}')


if __name__ == '__main__':
    main()
