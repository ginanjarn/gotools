"""completion assistant"""


import os
import logging
from .terminal import execute


logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(levelname)s\t%(module)s: %(lineno)d\t%(message)s"))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)


class CompletionError(Exception):
    """CompletionError"""


sys_envs = os.environ.copy()
GOPATH = sys_envs.get("GOPATH")


def make_completion(messages: str):
    """make completion"""

    def parse(messages: str):
        for messages in messages.splitlines():
            cols = messages.split(",,")
            yield {
                "type": cols[0],
                "name": cols[1],
                "signature": cols[2],
                "module": cols[3],
            }

    return list(parse(messages))


def complete(source: str, offset: int, workdir: str = None) -> "Dict[str, Any]":
    """complete"""

    try:
        command = [
            "gocode",
            "-f=csv",
            "-builtin",
            "-unimported-packages",
            "autocomplete",
            "c%s" % offset,
        ]
        result, ret_code = execute(
            command,
            stdin=source,
            workdir=GOPATH,
        )
    except FileNotFoundError as err:
        raise CompletionError from err
    else:
        sout, serr = result
        if ret_code != 0:
            raise CompletionError("\n".join(serr.decode().splitlines()))
        return make_completion(sout.decode())
