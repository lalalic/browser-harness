"""Browser Workspace ownership/recovery extensions stay independently testable here."""
import ast
import marshal
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DAEMON_SOURCE = ROOT / "src/browser_harness/daemon.py"


def isolated_functions(*names):
    source = ast.parse(DAEMON_SOURCE.read_text())
    nodes = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    return compile(ast.Module(body=nodes, type_ignores=[]), str(DAEMON_SOURCE), "exec")


def test_owner_identity_rejects_pid_reuse():
    ns = {"OWNER_PID": 123, "OWNER_STARTED": "generation-a", "_process_start_time": lambda _pid: "generation-b"}
    exec(isolated_functions("owner_alive"), ns)
    assert ns["owner_alive"]() is False
    ns["_process_start_time"] = lambda _pid: "generation-a"
    assert ns["owner_alive"]() is True
    ns["OWNER_STARTED"] = None
    assert ns["owner_alive"]() is False


def test_owner_pipe_eof_exits_harness_generation():
    if os.name != "posix":
        pytest.skip("POSIX inherited-pipe ownership")
    code = isolated_functions("monitor_owner")
    watcher = """
import marshal, os, sys
OWNER_TOKEN='owned'
OWNER_PIPE_FD=int(sys.argv[1])
owner_alive=lambda: True
exec(marshal.loads(bytes.fromhex(sys.argv[2])), globals())
monitor_owner()
"""
    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen([sys.executable, "-c", watcher, str(read_fd), marshal.dumps(code).hex()], pass_fds=(read_fd,))
    os.close(read_fd)
    try:
        assert proc.poll() is None
        os.close(write_fd)
        write_fd = -1
        assert proc.wait(timeout=4) == 0
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        if proc.poll() is None:
            proc.kill(); proc.wait()


def test_owner_shutdown_requires_exact_token():
    source = DAEMON_SOURCE.read_text()
    assert 'if meta == "owner_shutdown"' in source
    assert 'req.get("owner_token") != OWNER_TOKEN' in source
    assert '"error": "not harness owner"' in source


def test_explicit_cdp_spawn_uses_single_spawn_lock_and_inherited_pipe():
    source = (ROOT / "src/browser_harness/admin.py").read_text()
    assert "with _spawn_lock(name, timeout=startup_wait)" in source
    assert '"pass_fds": (int(e["BH_OWNER_PIPE_FD"]),)' in source


def test_transport_close_reconnect_path_is_serialized():
    source = DAEMON_SOURCE.read_text()
    assert "self._cdp_reconnect_lock = asyncio.Lock()" in source
    assert "async with self._cdp_reconnect_lock" in source
    assert "self._is_transport_closed(e)" in source
    assert "await self._reconnect_cdp(sid)" in source
