"""tools setup"""

import logging
import os
import subprocess
from collections import namedtuple
from typing import Union, List

LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)

ToolProperty = namedtuple("ToolProperty", ["name", "version"])
TOOLS = [
    ToolProperty("golang.org/x/tools/gopls", ""),
    ToolProperty("honnef.co/go/tools/cmd/staticcheck", ""),
]


def install_tools():

    for tool in TOOLS:
        tool_name = tool.name
        print(f"> installing {tool_name}")
        toolver = "latest" if not tool.version else tool.version
        command = ["go", "get", f"{tool_name}@{toolver}"]
        try:
            exec_cmd(command)
        except Exception as err:
            print(err)
        else:
            print(f"> {tool} installed")


def exec_cmd(command):
    startupinfo = None
    if os.name == "nt":
        # if on Windows, hide process window
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW

    LOGGER.debug("command: %s", command)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ,
        startupinfo=startupinfo,
    )
    poll = process.poll()
    if poll != 0:
        raise ValueError(f"install error with exit code {poll}")
