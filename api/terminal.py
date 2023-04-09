"""terminal helper"""

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import List, Any


if os.name == "nt":
    # if on Windows, hide process window
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
else:
    STARTUPINFO = None


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str


def flatten_command(command: List[str]) -> str:
    """flatten command arguments"""

    def wrap(arg: str):
        if (l := len(arg)) and l > 2:
            return f"{arg}"
        return arg

    return " ".join([wrap(c) for c in command])


def exec_cmd_nobuffer(command: List[str], **kwargs: Any) -> int:
    """exec command and write result to stderr

    return exit code
    """

    print(f"execute {flatten_command!r}")

    process = subprocess.Popen(
        command,
        # stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=STARTUPINFO,
        bufsize=0,
        cwd=kwargs.get("cwd"),
    )

    def listen_stderr():
        while True:
            if line := process.stderr.readline():
                print(line.strip().decode())
            else:
                return

    def listen_stdout():
        while True:
            if line := process.stdout.readline():
                print(line.strip().decode())
            else:
                return

    sout_thread = threading.Thread(target=listen_stdout, daemon=True)
    serr_thread = threading.Thread(target=listen_stderr, daemon=True)
    sout_thread.start()
    serr_thread.start()

    # wait until process done
    while process.poll() is None:
        time.sleep(0.5)

    return process.poll()
