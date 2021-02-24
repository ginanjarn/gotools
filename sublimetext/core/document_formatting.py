"""format prettier"""


from .terminal import execute


class FormattingError(Exception):
    """Formatting Error"""


def format_code(source: str):
    """format code

    Results:
        string formatted code"""

    try:
        result, ret_code = execute("goreturns", stdin=source)
    except FileNotFoundError as err:
        raise FormattingError(err)
    else:
        sout, serr = result
        if ret_code != 0:
            raise FormattingError(serr.decode())
        return sout.decode()
