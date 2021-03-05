"""documentation"""


import re
import os
from html import escape
from .terminal import execute
import logging


logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(levelname)s\t%(module)s: %(lineno)d\t%(message)s"))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)


class DocumentationError(Exception):
    """DocumentationError"""


sys_envs = os.environ.copy()
GOPATH = sys_envs.get("GOPATH")


def get_definition(source, offset, *, file_name=""):
    """get definition

    Results: definition
        => { 'path':'', 'line':0, 'column':0 }"""

    try:
        command = ["godef", "-i", "-o=%s" % offset, "-f='%s'" % file_name]
        result, ret_code = execute(command, stdin=source, workdir=GOPATH)
    except FileNotFoundError as err:
        raise DocumentationError(err)
    else:
        sout, serr = result
        if ret_code != 0:
            raise DocumentationError(serr.decode())

        found = re.findall(r"(.*):(\d*):(\d*)", sout.decode())
        if any(found):
            return {
                "path": found[0][0],
                "line": int(found[0][1]),
                "column": int(found[0][2]),
            }
        else:
            return None


def get_documentation(symbol):
    """get documentation

    Results:
        documentatation string"""

    try:
        command = ["go", "doc", "-short", "%s" % symbol]
        result, ret_code = execute(command, workdir=GOPATH)
    except FileNotFoundError as err:
        raise DocumentationError(err)
    else:
        sout, serr = result
        if ret_code != 0:
            raise DocumentationError("%s\n%s", symbol, serr.decode())
        return sout.decode()


def build_documentation(
    definition: "Dict[str,Any]", *, message: str = ""
) -> "Dict[str,Any]":
    """build documentation body

    Results:
        => { 'content':'', 'link':'' }"""

    def build_body(message: str):
        return escape(message, quote=False).replace("\n", "<br>")

    head = '<a href="">Go to definition</a>'
    content = (
        "{head}<p>{body}</p>".format(head=head, body=build_body(message))
        if message
        else "{head}".format(head=head)
    )
    return {
        "content": content,
        "link": {
            "path": definition["path"],
            "line": definition["line"],
            "character": definition["column"] - 1,
        },
    }
