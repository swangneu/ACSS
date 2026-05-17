"""Wrapper that launches matlab-mcp and filters non-JSON lines from its stdout.

The matlab-mcp package prints initialization messages (e.g. MATLAB_PATH info)
to stdout before starting JSON-RPC communication.  This breaks the MCP client
which expects only valid JSON-RPC on stdout.

This wrapper intercepts stdout, discards any line that isn't valid JSON, and
forwards the rest to the parent process's stdout.
"""

import json
import subprocess
import sys


def main() -> None:
    args = sys.argv[1:]
    proc = subprocess.Popen(
        [sys.executable, '-m', 'my_matlab_server.server', *args],
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        stripped = line.strip()
        if not stripped:
            continue
        # Only forward lines that look like JSON-RPC messages.
        if stripped.startswith('{') or stripped.startswith('['):
            print(stripped, flush=True)
        else:
            # Redirect non-JSON output to stderr so it doesn't break MCP.
            print(stripped, file=sys.stderr, flush=True)
    sys.exit(proc.wait())


if __name__ == '__main__':
    main()
