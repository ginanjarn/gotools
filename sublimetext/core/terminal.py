"""terminal interface"""


import os
import subprocess


def execute(
    command: str, *, stdin: str = None, workdir: str = None
) -> "Tuple[Any, int]":
    """execute terminal command

    Results:
    Tuple => ((stdout, stderr), returncode)"""

    process_cmd = command.split()
    env = os.environ.copy()

    if os.name == "nt":
        # linux subprocess module does not have STARTUPINFO
        # so only use it if on Windows
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
        process = subprocess.Popen(
            process_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            cwd=workdir,
            env=env,
            startupinfo=si,
        )
    else:
        process = subprocess.Popen(
            process_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            cwd=workdir,
            env=env,
        )

    return (
        process.communicate(stdin.encode()) if stdin else process.communicate(),
        process.returncode,
    )
