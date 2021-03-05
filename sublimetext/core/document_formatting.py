"""format prettier"""


import logging
from .terminal import execute


logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(levelname)s\t%(module)s: %(lineno)d\t%(message)s"))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)


class FormattingError(Exception):
    """Formatting Error"""


def format_code(source: str):
    """format code

    Results:
        string formatted code"""

    try:
        result, ret_code = execute(["goreturns"], stdin=source)
    except FileNotFoundError as err:
        raise FormattingError(err)
    else:
        sout, serr = result
        if ret_code != 0:
            raise FormattingError("\n".join(serr.decode().splitlines()))
        return sout.decode()
