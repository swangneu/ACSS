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
        help='Optional explicit path to Simulink template (.slx)',
    )
    parser.add_argument('--no-matlab', action='store_true', help='Disable MATLAB invocation and use synthetic simulator')
    args = parser.parse_args()

    orch = ACSSOrchestrator(
        args.requirements,
        args.out,
        use_matlab=not args.no_matlab,
        template_slx=args.template_slx,
    )
    run_dir = orch.run()
    print(f'Run complete: {run_dir}')


if __name__ == '__main__':
    main()
