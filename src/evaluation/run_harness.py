from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.contracts import load_requirements
from src.evaluation.waveform_harness import evaluate_waveform_files


def main() -> None:
    parser = argparse.ArgumentParser(description='Run ACSS waveform evaluation harness on waveform JSON files')
    parser.add_argument('--requirements', type=Path, required=True, help='Path to requirements JSON')
    parser.add_argument('--waveform', type=Path, action='append', required=True, help='Waveform JSON path (repeatable)')
    parser.add_argument('--out', type=Path, default=None, help='Optional output JSON path for harness report')
    args = parser.parse_args()

    req = load_requirements(args.requirements)
    report = evaluate_waveform_files(req, [str(path) for path in args.waveform])
    text = json.dumps(report, indent=2)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()

