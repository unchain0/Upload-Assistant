from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def _run_child(source: str) -> tuple[int, str]:
    with TemporaryDirectory() as state_dir:
        environment = {**os.environ, "UA_DATA_DIR": state_dir}
        result = subprocess.run(  # noqa: S603 - runs fixed local test code
            [sys.executable, "-c", source],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    return result.returncode, result.stdout + result.stderr


def test_first_cli_signal_requests_graceful_shutdown() -> None:
    code, output = _run_child(
        """
import signal
import upload
upload._reset_shutdown_state()
try:
    upload._handle_shutdown_signal(signal.SIGINT, None)
except KeyboardInterrupt:
    pass
assert upload._shutdown_requested
print('GRACEFUL_SIGNAL')
"""
    )

    assert code == 0, output
    assert "GRACEFUL_SIGNAL" in output
    assert "Received SIGINT" in output


def test_second_cli_signal_forces_exit() -> None:
    code, output = _run_child(
        """
import signal
import upload
upload._reset_shutdown_state()
try:
    upload._handle_shutdown_signal(signal.SIGINT, None)
except KeyboardInterrupt:
    pass
try:
    upload._handle_shutdown_signal(signal.SIGINT, None)
except SystemExit as error:
    assert error.code == 1
print('FORCED_EXIT')
"""
    )

    assert code == 0, output
    assert "FORCED_EXIT" in output
    assert "Forced exit" in output


def test_cli_maps_keyboard_interrupt_and_system_exit_codes() -> None:
    code, output = _run_child(
        """
import upload
assert upload._process_exit_code(KeyboardInterrupt()) == 130
assert upload._process_exit_code(SystemExit(2)) == 2
assert upload._process_exit_code(SystemExit('bad')) == 1
assert upload._process_exit_code(RuntimeError('failed')) == 1
print('EXIT_CODES_OK')
"""
    )

    assert code == 0, output
    assert "EXIT_CODES_OK" in output
