"""terminal interface"""


import os
import subprocess
import logging


logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(levelname)s\t%(module)s: %(lineno)d\t%(message)s"))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)


def execute(
    command: "List[str]", *, stdin: str = None, workdir: str = None
) -> "Tuple[Any, int]":
    """execute terminal command

    Results:
    Tuple => ((stdout, stderr), returncode)"""

    env = os.environ.copy()
    logger.debug("exec command : %s", command)

    if os.name == "nt":
        # linux subprocess module does not have STARTUPINFO
        # so only use it if on Windows
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
        process = subprocess.Popen(
            command,
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
            command,
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
