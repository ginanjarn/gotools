"""backend api"""


from collections import namedtuple
from html import escape
import itertools
import logging
import os
import re
import subprocess


logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
stream_formatter = "%(levelname)s %(asctime)s: %(name)s:%(lineno)s:  %(message)s"
sh.setFormatter(logging.Formatter(stream_formatter))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)


GocodeResult = namedtuple("GocodeResult", ["type_", "name", "data", "package"])


class Gocode:
    def parse_gocode_result(self, raw: str):
        for line in raw.splitlines():
            yield GocodeResult(*line.split(",,"))

    def gocode_exec(self, source: str, workdir: str, location: int):

        command = ["gocode", "-f=csv", "autocomplete", "c%s" % location]
        env = os.environ.copy()

        if os.name == "nt":
            # STARTUPINFO only available on windows
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
        else:
            startupinfo = None

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                shell=True,
                env=env,
                cwd=workdir,
            )
            sout, serr = process.communicate(source.encode("utf8"))
            if serr:
                logger.debug(
                    "gocode error:\n%s" % ("\n".join(serr.decode().splitlines()))
                )
                return

            yield from self.parse_gocode_result(sout.decode("utf8"))

        except OSError as err:
            logger.error(err)

    keywords = (
        GocodeResult("keyword", "break", "", ""),
        GocodeResult("keyword", "case", "", ""),
        GocodeResult("keyword", "chan", "", ""),
        GocodeResult("keyword", "const", "", ""),
        GocodeResult("keyword", "continue", "", ""),
        GocodeResult("keyword", "default", "", ""),
        GocodeResult("keyword", "defer", "", ""),
        GocodeResult("keyword", "else", "", ""),
        GocodeResult("keyword", "fallthrough", "", ""),
        GocodeResult("keyword", "for", "", ""),
        GocodeResult("keyword", "func", "", ""),
        GocodeResult("keyword", "go", "", ""),
        GocodeResult("keyword", "goto", "", ""),
        GocodeResult("keyword", "if", "", ""),
        GocodeResult("keyword", "import", "", ""),
        GocodeResult("keyword", "interface", "", ""),
        GocodeResult("keyword", "map", "", ""),
        GocodeResult("keyword", "package", "", ""),
        GocodeResult("keyword", "range", "", ""),
        GocodeResult("keyword", "return", "", ""),
        GocodeResult("keyword", "select", "", ""),
        GocodeResult("keyword", "struct", "", ""),
        GocodeResult("keyword", "switch", "", ""),
        GocodeResult("keyword", "type", "", ""),
        GocodeResult("keyword", "var", "", ""),
    )

    builtin_results = (
        GocodeResult("const", "true", "", "builtin"),
        GocodeResult("const", "iota", "", "builtin"),
        GocodeResult("func", "close", "func(c chan<- Type)", "builtin"),
        GocodeResult("func", "delete", "func(m map[Type]Type1, key Type)", "builtin"),
        GocodeResult("func", "panic", "func(v interface{})", "builtin"),
        GocodeResult("func", "print", "func(args ...Type)", "builtin"),
        GocodeResult("func", "println", "func(args ...Type)", "builtin"),
        GocodeResult("func", "recover", "func() interface{}", "builtin"),
        GocodeResult("func", "cap", "func(v Type) int", "builtin"),
        GocodeResult("func", "copy", "func(dst, src []Type) int", "builtin"),
        GocodeResult("func", "len", "func(v Type) int", "builtin"),
        GocodeResult("type", "ComplexType", "", "builtin"),
        GocodeResult("func", "complex", "func(r, i FloatType) ComplexType", "builtin"),
        GocodeResult("type", "FloatType", "", "builtin"),
        GocodeResult("func", "imag", "func(c ComplexType) FloatType", "builtin"),
        GocodeResult("func", "real", "func(c ComplexType) FloatType", "builtin"),
        GocodeResult("type", "IntegerType", "", "builtin"),
        GocodeResult("type", "Type", "", "builtin"),
        GocodeResult("var", "nil", "", "builtin"),
        GocodeResult(
            "func", "append", "func(slice []Type, elems ...Type) []Type", "builtin"
        ),
        GocodeResult(
            "func", "make", "func(t Type, size ...IntegerType) Type", "builtin"
        ),
        GocodeResult("func", "new", "func(Type) *Type", "builtin"),
        GocodeResult("type", "Type1", "", "builtin"),
        GocodeResult("type", "bool", "", "builtin"),
        GocodeResult("type", "byte", "", "builtin"),
        GocodeResult("type", "complex128", "", "builtin"),
        GocodeResult("type", "complex64", "", "builtin"),
        GocodeResult("type", "error", "", "builtin"),
        GocodeResult("type", "float32", "", "builtin"),
        GocodeResult("type", "float64", "", "builtin"),
        GocodeResult("type", "int", "", "builtin"),
        GocodeResult("type", "int16", "", "builtin"),
        GocodeResult("type", "int32", "", "builtin"),
        GocodeResult("type", "int64", "", "builtin"),
        GocodeResult("type", "int8", "", "builtin"),
        GocodeResult("type", "rune", "", "builtin"),
        GocodeResult("type", "string", "", "builtin"),
        GocodeResult("type", "uint", "", "builtin"),
        GocodeResult("type", "uint16", "", "builtin"),
        GocodeResult("type", "uint32", "", "builtin"),
        GocodeResult("type", "uint64", "", "builtin"),
        GocodeResult("type", "uint8", "", "builtin"),
        GocodeResult("type", "uintptr", "", "builtin"),
    )

    def __init__(self, source: str, workdir: str):
        self.source = source
        self.workdir = workdir

    def complete(self, offset: int):
        *_, last_line = self.source[:offset].splitlines()

        if re.match(r"(?:.*)(\w+)(?:\.\w*)$", last_line):
            yield from self.gocode_exec(
                self.source, workdir=self.workdir, location=offset
            )
            return

        candidates = itertools.chain(
            self.gocode_exec(self.source, workdir=self.workdir, location=offset),
            self.keywords,
            self.builtin_results,
        )

        if re.match(
            r"(?:.*func.*)([\(\,]\s*\w+\s+\w*)?(\)(?:\s*\w*\s*\,*)*)$", last_line,
        ):
            for completion in candidates:
                if completion.type_ == "type":
                    yield completion

            return

        yield from candidates

    def get_documentation(self, offset: int):

        candidates = itertools.chain(
            self.gocode_exec(self.source, workdir=self.workdir, location=offset),
            self.keywords,
            self.builtin_results,
        )

        *_, last_line = self.source[:offset].splitlines()

        match = re.match(r"func\s+(\w+)\.*$", last_line,)
        if match:
            return None

        match = re.match(
            r".*[\/\\\(\)\"\'\-\:\,\.\;\<\>\~\!\@\#\$\%\^\&\*\|\+\=\[\]\{\}\`\~\?](\w+)$",
            last_line,
        )
        if match:
            name = match.group(1)

        else:
            *_, last_word = last_line.split()
            name = last_word

        for candidate in candidates:
            if candidate.name == name:
                return candidate


class Documentation:
    def __init__(self, doc: str, *, package: str = "", methodOrField: str = ""):
        self.documentation = doc
        self.pkg_methodOrField = "%s%s" % (
            package,
            "" if not methodOrField else "%s" % methodOrField,
        )

    def to_html(self):
        if not self.documentation:
            return ""

        return "<div style='border: 0.5em;display: block'>{doc}<br><a href='{link}'>More...</a></div>".format(
            doc=self.documentation, link=self.pkg_methodOrField,
        )

    @classmethod
    def from_gocoderesult(cls, gocode_result):

        logger.debug(gocode_result)
        if not gocode_result:
            return cls(doc="")

        if (gocode_result.name == "main") or (gocode_result.data == "invalid type"):
            return cls(doc="")

        if gocode_result.type_ == "package":
            doc = "package <strong>%s</strong>" % (gocode_result.name)
            return cls(doc, package=gocode_result.name)

        package = "%s." % gocode_result.package if gocode_result.package else ""

        if gocode_result.type_ == "func":
            doc = "<i>%s</i><strong>%s</strong>%s" % (
                package,
                gocode_result.name,
                gocode_result.data[4:],
            )
            return cls(doc, package=package, methodOrField=gocode_result.name)

        doc = "<i>%s</i><strong>%s</strong> %s" % (
            package,
            gocode_result.name,
            gocode_result.data,
        )
        return cls(doc, package=package, methodOrField=gocode_result.name)


class Godoc:
    """get documentation from godoc"""

    @staticmethod
    def get_godoc(methodOrField: str, workdir: str):

        command = ["go", "doc", methodOrField]
        env = os.environ.copy()

        if os.name == "nt":
            # STARTUPINFO only available on windows
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
        else:
            startupinfo = None

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                shell=True,
                env=env,
                cwd=workdir,
            )
            sout, serr = process.communicate()
            if serr:
                logger.debug(
                    "go doc error:\n%s" % ("\n".join(serr.decode().splitlines()))
                )
                return ""

            return sout.decode("utf8")

        except OSError as err:
            logger.error(err)

    def __init__(self, methodOrField: str, workdir: str):
        self.documentation = self.get_godoc(methodOrField, workdir)

    def to_html(self):
        if not self.documentation:
            return ""

        html_escaped = escape(self.documentation)
        tab_expanded = html_escaped.expandtabs(4)
        space_replaced = tab_expanded.replace(" ", "&nbsp;")  # non-breakable space
        paragraph_wrapped = "".join(
            ("<p>%s</p>" % lines for lines in space_replaced.split("\n\n"))
        )
        break_lines = "<br>".join(paragraph_wrapped.splitlines())
        return "<div style='border: 0.5em;display: block'>%s</div>" % break_lines


def get_completion(source: str, workdir: str, location: int):
    gocode = Gocode(source, workdir)
    yield from gocode.complete(location)


def get_documentation(source: str, workdir: str, location: int):
    gocode = Gocode(source, workdir)
    return Documentation.from_gocoderesult(gocode.get_documentation(location)).to_html()


def get_godoc_documentation(methodOrField: str, workdir: str):
    godoc = Godoc(methodOrField, workdir)
    return godoc.to_html()


def get_formatted_code(source: str):

    command = ["gofmt"]
    env = os.environ.copy()

    if os.name == "nt":
        # STARTUPINFO only available on windows
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
    else:
        startupinfo = None

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            shell=True,
            env=env,
            # cwd=workdir,
        )
        sout, serr = process.communicate(source.encode("utf8"))
        if serr:
            logger.debug(
                "completion error:\n%s" % ("\n".join(serr.decode().splitlines()))
            )
            raise ValueError("\n".join(serr.decode().splitlines()))
            # return None
        return sout.decode("utf8")

    except OSError as err:
        logger.error(err)


def get_diagnostic(path: str, workdir: str = ""):
    """get diagnostic for file or directory"""

    command = ["go", "vet", path]
    env = os.environ.copy()

    if os.name == "nt":
        # STARTUPINFO only available on windows
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
    else:
        startupinfo = None

    if not workdir:
        workdir = os.path.dirname(path) if os.path.isfile(path) else path

    try:
        process = subprocess.Popen(
            command,
            # stdin=subprocess.PIPE,
            # stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            shell=True,
            env=env,
            cwd=workdir,
        )

        _, serr = process.communicate()
        return serr.decode("utf8")

    except OSError as err:
        logger.error(err)
