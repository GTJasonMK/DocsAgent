from __future__ import annotations

import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "start.sh requires Bash")
class StartScriptTests(unittest.TestCase):
    def test_uses_backend_ready_signal_and_actual_bound_port(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            tools = temp_root / "tools"
            tools.mkdir()
            python = temp_root / "python"
            python.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys, time\n"
                "if sys.argv[1:2] == ['-c']:\n"
                f"    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
                "print('DOCSAGENT_READY 127.0.0.1 31415', flush=True)\n"
                "deadline = time.monotonic() + 2\n"
                "while not os.path.exists(os.environ['OPEN_MARKER']) and time.monotonic() < deadline:\n"
                "    time.sleep(0.01)\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            opened = temp_root / "opened-url"
            opener = tools / "xdg-open"
            opener.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\" > \"$OPEN_MARKER\"\n", encoding="utf-8")
            opener.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "DOCSAGENT_PYTHON": str(python),
                "DOCSAGENT_PORT": "0",
                "OPEN_MARKER": str(opened),
                "PATH": str(tools) + os.pathsep + env["PATH"],
            })
            result = subprocess.run(
                ["bash", str(ROOT / "start.sh")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(opened.read_text(encoding="utf-8").strip(), "http://127.0.0.1:31415")

    def test_does_not_treat_another_service_as_docsagent(self) -> None:
        with socket.socket() as listener, tempfile.TemporaryDirectory() as directory:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            temp_root = Path(directory)
            tools = temp_root / "tools"
            tools.mkdir()
            python = temp_root / "python"
            python.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "if sys.argv[1:2] == ['-c']:\n"
                f"    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            opened = temp_root / "opened-url"
            opener = tools / "xdg-open"
            opener.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\" > \"$OPEN_MARKER\"\n", encoding="utf-8")
            opener.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "DOCSAGENT_PYTHON": str(python),
                "DOCSAGENT_PORT": str(port),
                "OPEN_MARKER": str(opened),
                "PATH": str(tools) + os.pathsep + env["PATH"],
            })
            result = subprocess.run(
                ["bash", str(ROOT / "start.sh")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(opened.exists())

    def test_interrupt_reaps_the_backend_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            tools = temp_root / "tools"
            tools.mkdir()
            python = temp_root / "python"
            child_pid_file = temp_root / "backend-pid"
            python.write_text(
                "#!/usr/bin/env python3\n"
                "import os, signal, sys, time\n"
                "if sys.argv[1:2] == ['-c']:\n"
                f"    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
                "with open(os.environ['CHILD_PID_FILE'], 'w', encoding='utf-8') as file:\n"
                "    file.write(str(os.getpid()))\n"
                "signal.signal(signal.SIGINT, lambda *_: sys.exit(0))\n"
                "print('DOCSAGENT_READY 127.0.0.1 31415', flush=True)\n"
                "while True:\n"
                "    time.sleep(0.05)\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            opened = temp_root / "opened-url"
            opener = tools / "xdg-open"
            opener.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\" > \"$OPEN_MARKER\"\n", encoding="utf-8")
            opener.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "DOCSAGENT_PYTHON": str(python),
                "DOCSAGENT_PORT": "0",
                "OPEN_MARKER": str(opened),
                "CHILD_PID_FILE": str(child_pid_file),
                "PATH": str(tools) + os.pathsep + env["PATH"],
            })
            process = subprocess.Popen(
                ["bash", str(ROOT / "start.sh")],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not opened.exists() and process.poll() is None:
                    time.sleep(0.02)
                self.assertTrue(opened.exists(), "start.sh did not reach the ready state")
                child_pid = int(child_pid_file.read_text(encoding="utf-8"))
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130, stderr)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    process.communicate(timeout=5)
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
