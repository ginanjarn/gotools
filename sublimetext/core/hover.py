"""documentation"""


import re
import os
from html import escape
from .terminal import execute


class DocumentationError(Exception):
    """DocumentationError"""


def get_definition(source, offset, *, file_name=""):
    """get definition

    Results: definition
        => { 'path':'', 'line':0, 'column':0 }"""

    try:
        result, ret_code = execute(
            "godef -i -o=%s -f=%s" % (offset, file_name), stdin=source, workdir=None
        )
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
        result, ret_code = execute("go doc -short %s" % symbol)
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
        "<div>{head}<p>{body}</p></div>".format(head=head, body=build_body(message))
        if message
        else "<div>{head}</div>".format(head=head)
    )
    return {
        "content": content,
        "link": {
            "path": definition["path"],
            "line": definition["line"],
            "character": definition["column"] - 1,
        },
    }
