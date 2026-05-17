"""Lightweight MCP server that wraps `matlab -batch`.

Unlike matlab-mcp (which requires the MATLAB Python Engine and Python ≤3.12),
this server uses `matlab -batch` to execute MATLAB code, so it works with any
Python version.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


app = Server('acss-matlab')


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name='execute_matlab_code',
            description='Execute MATLAB code via matlab -batch and return stdout/stderr.',
            inputSchema={
                'type': 'object',
                'properties': {
                    'code': {
                        'type': 'string',
                        'description': 'MATLAB code to execute.',
                    },
                },
                'required': ['code'],
            },
        ),
        Tool(
            name='execute_matlab_script',
            description='Execute a saved MATLAB script by name.',
            inputSchema={
                'type': 'object',
                'properties': {
                    'script_name': {
                        'type': 'string',
                        'description': 'Name of the MATLAB script (without .m).',
                    },
                },
                'required': ['script_name'],
            },
        ),
        Tool(
            name='create_matlab_script',
            description='Create a MATLAB script file.',
            inputSchema={
                'type': 'object',
                'properties': {
                    'script_name': {
                        'type': 'string',
                        'description': 'Name of the script (without .m).',
                    },
                    'code': {
                        'type': 'string',
                        'description': 'MATLAB code for the script body.',
                    },
                },
                'required': ['script_name', 'code'],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == 'execute_matlab_code':
        code = arguments.get('code', '')
        return _run_matlab_batch(code)

    if name == 'create_matlab_script':
        script_name = arguments.get('script_name', 'untitled')
        code = arguments.get('code', '')
        return _create_script(script_name, code)

    if name == 'execute_matlab_script':
        script_name = arguments.get('script_name', '')
        return _execute_script(script_name)

    return [TextContent(type='text', text=f'Unknown tool: {name}')]


def _run_matlab_batch(code: str) -> list[TextContent]:
    matlab_exe = shutil.which('matlab')
    if not matlab_exe:
        return [TextContent(type='text', text='Error: matlab not found on PATH')]

    try:
        result = subprocess.run(
            [matlab_exe, '-batch', code],
            capture_output=True,
            text=True,
            timeout=300,
            env=os.environ,
        )
        output = result.stdout or ''
        if result.stderr:
            output += '\n--- STDERR ---\n' + result.stderr
        if result.returncode != 0:
            output += f'\n--- EXIT CODE: {result.returncode} ---'
        return [TextContent(type='text', text=output)]
    except subprocess.TimeoutExpired:
        return [TextContent(type='text', text='Error: MATLAB execution timed out (300s)')]
    except Exception as e:
        return [TextContent(type='text', text=f'Error: {e}')]


def _create_script(script_name: str, code: str) -> list[TextContent]:
    script_dir = Path(tempfile.mkdtemp(prefix='acss_mcp_'))
    script_path = script_dir / f'{script_name}.m'
    script_path.write_text(code, encoding='utf-8')
    return [TextContent(type='text', text=f'Script created: {script_path}')]


def _execute_script(script_name: str) -> list[TextContent]:
    # Search for the script in common locations.
    search_dirs = [Path.cwd(), Path(tempfile.gettempdir())]
    for d in search_dirs:
        for p in d.rglob(f'{script_name}.m'):
            return _run_matlab_batch(f"run('{p.as_posix()}')")
    return [TextContent(type='text', text=f'Script not found: {script_name}.m')]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
