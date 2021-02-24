"""completion assistant"""


from .terminal import execute


class CompletionError(Exception):
    """CompletionError"""


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
        result, ret_code = execute(
            "gocode -f=csv -builtin -unimported-packages autocomplete c%s" % offset,
            stdin=source,
        )
    except FileNotFoundError as err:
        raise CompletionError from err
    else:
        sout, serr = result
        if ret_code != 0:
            raise CompletionError(serr.decode())
        return make_completion(sout.decode())
