"""MATLAB execution backends for ACSS.

Provides a strategy-pattern abstraction for running MATLAB/Simulink simulations.
Two implementations:

* ``BatchBackend`` — spawns ``matlab -batch`` for each simulation (existing behavior).
* ``MCPBackend`` — connects to a persistent MATLAB MCP server via the MCP Python SDK,
  eliminating cold-start overhead and enabling future use of Simulink Agentic Toolkit
  skills (model inspection, profiling, debugging).

Use ``create_backend()`` to instantiate the right backend based on user preference.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.contracts import SimulationResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class MatlabBackend(ABC):
    """Abstract interface for executing MATLAB simulations."""

    @abstractmethod
    def execute(
        self,
        payload_path: Path,
        out_dir: Path,
        template_slx: Path | None = None,
    ) -> SimulationResult:
        """Run ``acss_build_and_run`` via this backend and return a SimulationResult."""
        ...

    def close(self) -> None:
        """Release resources (no-op by default). Override if needed."""

    def is_available(self) -> bool:
        """Return True if this backend can accept work right now."""
        return True


# ---------------------------------------------------------------------------
# Batch backend (existing behavior)
# ---------------------------------------------------------------------------

class BatchBackend(MatlabBackend):
    """Backend that spawns ``matlab -batch`` for each simulation.

    Delegates to :func:`src.matlab_bridge.run_matlab_stub`.  The import is lazy
    so that the MCP code path never touches ``subprocess`` / ``shutil.which``.
    """

    def execute(
        self,
        payload_path: Path,
        out_dir: Path,
        template_slx: Path | None = None,
    ) -> SimulationResult:
        from src.matlab_bridge import run_matlab_stub  # noqa: F811

        return run_matlab_stub(payload_path, out_dir, template_slx)

    def is_available(self) -> bool:
        return shutil.which('matlab') is not None


# ---------------------------------------------------------------------------
# MCP backend (persistent MATLAB MCP server)
# ---------------------------------------------------------------------------

class MCPBackend(MatlabBackend):
    """Backend that uses a persistent MATLAB MCP server session.

    Connects to ``matlab-mcp`` over stdio using the MCP Python SDK.
    The SDK is async; this class maintains a dedicated ``asyncio`` event loop in
    a background daemon thread and bridges synchronous calls via
    ``asyncio.run_coroutine_threadsafe()``.

    The existing ``acss_build_and_run.m`` script is called via the
    ``evaluate_matlab_code`` MCP tool — zero changes to the MATLAB side.
    """

    # Timeout for the initial MCP handshake.
    _CONNECT_TIMEOUT_S: float = 30.0
    # Timeout for a single simulation call (5 minutes).
    _EXECUTE_TIMEOUT_S: float = 300.0

    def __init__(
        self,
        server_command: str | None = None,
        matlab_display_mode: str = 'nodesktop',
        initial_working_folder: str | None = None,
    ) -> None:
        self._server_command = server_command or _find_matlab_mcp()
        self._matlab_display_mode = matlab_display_mode
        self._initial_working_folder = initial_working_folder or str(Path.cwd())

        # Populated lazily by _ensure_session().
        self._session: Any = None
        self._context_manager: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._connected: bool = False
        self._connect_attempted: bool = False

    def _server_command_args(self) -> list[str]:
        return [
            f'--matlab-display-mode={self._matlab_display_mode}',
            f'--initial-working-folder={self._initial_working_folder}',
        ]

    # -- public interface ---------------------------------------------------

    def execute(
        self,
        payload_path: Path,
        out_dir: Path,
        template_slx: Path | None = None,
    ) -> SimulationResult:
        self._ensure_session()
        if not self._connected:
            raise RuntimeError(
                'MCP backend not connected. Ensure the MATLAB MCP server is installed '
                'and running. Install with: pip install mcp; server: matlab-mcp'
            )

        payload_path = payload_path.resolve()
        out_dir = out_dir.resolve()
        out_json = (out_dir / 'matlab_result.json').resolve()
        template_arg = template_slx.resolve().as_posix() if template_slx is not None else ''

        # Same MATLAB code as the -batch argument in BatchBackend / matlab_bridge.
        project_root = Path(__file__).resolve().parent.parent
        matlab_dir = (project_root / 'matlab').as_posix()
        matlab_code = (
            f"cd('{out_dir.as_posix()}'); "
            f"addpath('{matlab_dir}'); "
            f"acss_build_and_run("
            f"'{payload_path.as_posix()}',"
            f"'{out_json.as_posix()}',"
            f"'{template_arg}')"
        )

        # Execute MATLAB code directly via the MCP tool.
        tool_name = self._find_eval_tool()
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool(tool_name, {'code': matlab_code}),
            self._loop,
        )
        result = future.result(timeout=self._EXECUTE_TIMEOUT_S)

        # Persist MCP tool output for debugging.
        if hasattr(result, 'content'):
            for item in result.content:
                text = getattr(item, 'text', '')
                if text:
                    (out_dir / 'matlab_mcp_output.log').write_text(
                        text, encoding='utf-8',
                    )

        if not out_json.exists():
            raise RuntimeError(
                f'MATLAB MCP ran but produced no output file at {out_json}. '
                f'Check matlab_mcp_output.log in {out_dir}.'
            )

        data = json.loads(out_json.read_text(encoding='utf-8'))
        code_files = data.get('code_files', [])
        return SimulationResult(
            metrics=data['metrics'],
            waveform_files=data['waveform_files'],
            code_files=code_files,
            raw=data,
            waveform_image_files=data.get('waveform_image_files', []),
        )

    def is_available(self) -> bool:
        self._ensure_session()
        return self._connected

    def close(self) -> None:
        self._cleanup_loop()

    # -- internal -----------------------------------------------------------

    def _ensure_session(self) -> None:
        """Lazily establish the MCP session.  Runs at most once."""
        if self._connect_attempted:
            return
        self._connect_attempted = True

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            logger.warning(
                'MCP SDK not installed (pip install mcp). '
                'MCP backend unavailable; will fall back to batch mode.'
            )
            return

        # Auto-detect MATLAB installation directory for the MCP server.
        matlab_path = os.environ.get('MATLAB_PATH', '')
        if not matlab_path:
            matlab_exe = shutil.which('matlab')
            if matlab_exe:
                matlab_path = str(Path(matlab_exe).parent.parent)

        env = {**os.environ}
        if matlab_path:
            env['MATLAB_PATH'] = matlab_path

        # Use the ACSS lightweight MCP server (python matlab_mcp_server.py).
        acss_server = Path(__file__).resolve().parent.parent / 'matlab_mcp_server.py'
        if acss_server.exists():
            cmd = sys.executable
            cmd_args = [str(acss_server)]
        else:
            cmd = self._server_command
            cmd_args = self._server_command_args()

        server_params = StdioServerParameters(
            command=cmd,
            args=cmd_args,
            env=env,
        )

        # Dedicated event loop in a daemon thread.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True,
        )
        self._loop_thread.start()

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._connect_async(stdio_client, ClientSession, server_params),
                self._loop,
            )
            future.result(timeout=self._CONNECT_TIMEOUT_S)
            self._connected = True
            logger.info('MCP backend connected to MATLAB MCP server.')
        except Exception as exc:
            logger.warning('MCP connection failed: %s. Falling back.', exc)
            self._cleanup_loop()

    async def _connect_async(
        self,
        stdio_client_fn: Any,
        session_cls: Any,
        server_params: Any,
    ) -> None:
        """Async helper — sets up transport + session."""
        self._context_manager = stdio_client_fn(server_params)
        read, write = await self._context_manager.__aenter__()
        self._session = session_cls(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Async helper — calls a single MCP tool."""
        return await self._session.call_tool(tool_name, arguments=arguments)

    def _find_eval_tool(self) -> str:
        """Find the tool name for evaluating MATLAB code."""
        if self._session is None:
            return 'execute_matlab_script'
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._session.list_tools(), self._loop,
            )
            result = future.result(timeout=10)
            names = {t.name for t in result.tools}
            for candidate in ('execute_matlab_code', 'evaluate_matlab_code', 'execute_matlab_script', 'call_matlab_function'):
                if candidate in names:
                    return candidate
        except Exception:
            pass
        return 'execute_matlab_script'

    def _cleanup_loop(self) -> None:
        """Tear down the event loop, session, and transport."""
        if self._loop is None or not self._loop.is_running():
            self._connected = False
            return

        # Close session and transport gracefully.
        if self._session is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._session.__aexit__(None, None, None), self._loop,
                ).result(timeout=5)
            except Exception:
                pass
        if self._context_manager is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._context_manager.__aexit__(None, None, None), self._loop,
                ).result(timeout=5)
            except Exception:
                pass

        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5)
        self._connected = False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _find_matlab_mcp() -> str:
    """Locate the ACSS MATLAB MCP server script."""
    # Prefer the lightweight ACSS server (no MATLAB Python Engine dependency).
    acss_server = Path(__file__).resolve().parent.parent / 'matlab_mcp_server.py'
    if acss_server.exists():
        return str(acss_server)
    # Fall back to the matlab-mcp package.
    found = shutil.which('matlab-mcp')
    if found:
        return found
    import sys
    scripts_dir = Path(sys.executable).parent / 'Scripts'
    for name in ('matlab-mcp.exe', 'matlab-mcp'):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate)
    return 'matlab-mcp'


def create_backend(
    preference: str = 'auto',
    mcp_server_command: str | None = None,
    mcp_working_folder: str | None = None,
) -> MatlabBackend:
    """Create the appropriate MATLAB backend.

    Args:
        preference: ``"mcp"``, ``"batch"``, or ``"auto"`` (try MCP, fall back
            to batch).
        mcp_server_command: Command or full path to the MATLAB MCP server binary.
            If None, auto-detected.
        mcp_working_folder: Initial working folder for the MATLAB MCP session.

    Returns:
        A ready-to-use :class:`MatlabBackend`.

    Raises:
        RuntimeError: If ``preference="mcp"`` and the MCP server is unavailable.
    """
    if preference == 'batch':
        return BatchBackend()

    if mcp_server_command is None:
        mcp_server_command = _find_matlab_mcp()

    mcp = MCPBackend(
        server_command=mcp_server_command,
        initial_working_folder=mcp_working_folder,
    )

    if preference == 'mcp':
        if not mcp.is_available():
            raise RuntimeError(
                'MCP backend requested but could not connect to MATLAB MCP server. '
                'Ensure \'matlab-mcp\' is installed and MATLAB is available.'
            )
        return mcp

    # auto: try MCP, fall back silently.
    if mcp.is_available():
        return mcp
    logger.info('MCP backend unavailable; using batch mode.')
    return BatchBackend()
