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

    def gocode_exec(self, source: str, file_path: str, location: int, ignore_case=True):

        command = [
            "gocode",
            "-builtin",
            "-f=csv",
            "autocomplete",
            file_path,
            "c%s" % location,
        ]

        if ignore_case:
            command.insert(1, "-ignore-case")

        env = os.environ
        workdir = os.path.dirname(file_path)

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

    def __init__(self, source: str, file_path: str):
        self.source = source
        self.file_path = file_path

    def complete(self, offset: int):
        *_, last_line = self.source[:offset].splitlines()

        # access member
        if re.search(r"\w+[\w\)\]]?\.\w*$", last_line):
            yield from self.gocode_exec(
                self.source, file_path=self.file_path, location=offset,
            )
            return

        candidates = itertools.chain(
            self.gocode_exec(self.source, file_path=self.file_path, location=offset),
            self.keywords,
        )

        yield from candidates

    def get_documentation(self, offset: int):

        candidates = itertools.chain(
            self.gocode_exec(
                self.source,
                file_path=self.file_path,
                location=offset,
                ignore_case=False,
            ),
            self.keywords,
        )

        *_, last_line = self.source[:offset].splitlines()

        match = re.search(r"\W(\w+)$", last_line,)
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
        self.pkg_methodOrField = ".".join([package, methodOrField])

    def to_html(self):
        if not self.documentation:
            return ""

        return "<div style='border: 0.5em;display: block'>{doc}<a href='{link}'>More...</a></div>".format(
            doc=self.documentation, link=self.pkg_methodOrField,
        )

    @classmethod
    def from_gocoderesult(cls, gocode_result):

        logger.debug(gocode_result)

        if not gocode_result:
            return cls(doc="")

        if (gocode_result.name == "main") or (gocode_result.data == "invalid type"):
            return cls(doc="")

        package = gocode_result.package if gocode_result.package else ""

        if gocode_result.type_ == "package":
            package = gocode_result.name

        package_str = "package: <strong>%s</strong>" % package if package else ""

        if gocode_result.type_ == "package":
            doc = "<p>%s</p>" % (package_str)
            return cls(doc, package=package)

        annotation = (
            escape(gocode_result.data[4:])
            if gocode_result.type_ == "func"
            else gocode_result.data
        )

        doc = "<em>%s</em><p><strong>%s</strong> %s</p>" % (
            package_str,
            gocode_result.name,
            annotation,
        )
        return cls(doc, package=package, methodOrField=gocode_result.name)


class Godoc:
    """get documentation from godoc"""

    @staticmethod
    def get_godoc(methodOrField: str, workdir: str):

        command = ["go", "doc", methodOrField]
        logger.debug(command)

        env = os.environ

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

        pkg, identifier = methodOrField.split(".")

        if (not pkg) and (not str.istitle(identifier)):
            # this case for local not exported method or field
            self.documentation = ""

        elif not pkg:
            # this case for local exported method of field
            self.documentation = self.get_godoc(identifier, workdir)

        elif not identifier:
            # this case for package without identifier
            self.documentation = self.get_godoc(pkg, workdir)

        else:
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


def get_completion(source: str, file_path: str, location: int):
    gocode = Gocode(source, file_path)
    yield from gocode.complete(location)


def get_documentation(source: str, file_path: str, location: int):
    gocode = Gocode(source, file_path)
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
