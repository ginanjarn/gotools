"""backend api"""


from collections import namedtuple
from html import escape
from io import StringIO
import itertools
import logging
import os
import json
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


class Gogetdoc:
    """get documentation from gogetdoc"""

    def gogetdoc_exec(self, source: str, file_path: str, location: int):

        pos = "%s:#%d" % (file_path, location)
        source_encoded = source.encode("utf8")
        guru_archive = "%s\n%s\n%s" % (file_path, len(source_encoded), source)

        command = ["gogetdoc", "-modified", "-json", "-pos", pos]
        env = os.environ
        workdir = os.path.dirname(file_path)

        logger.debug("cmd:%s", command)
        logger.debug("dirname:%s", workdir)

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
            sout, serr = process.communicate(guru_archive.encode("utf8"))
            if serr:
                logger.debug(
                    "gogetdoc error:\n%s" % ("\n".join(serr.decode().splitlines()))
                )
                return ""
            logger.debug(sout.decode())
            return sout.decode("utf8")

        except OSError as err:
            logger.error(err)

    def __init__(self, source: str, file_path: str):
        self.source = source
        self.file_path = file_path

    def get_documentation(self, offset: int):
        return self.gogetdoc_exec(self.source, self.file_path, offset)


class Documentation:
    def __init__(self, doc: str):
        self.documentation = doc

    def to_html(self):
        if not self.documentation:
            return ""

        return "<div style='border: 0.5em;display: block'>{doc}</div>".format(
            doc=self.documentation,
        )

    @staticmethod
    def translate_space(src: str) -> str:
        return (
            src.replace("\t", "&nbsp;&nbsp;")
            .replace("  ", "&nbsp;&nbsp;")
            .replace("\n", "<br>")
        )

    @classmethod
    def from_gogetdocresult(cls, doc_result):

        # {
        #   "name": "RuneCountInString",
        #   "import": "unicode/utf8",
        #   "pkg": "utf8",
        #   "decl": "func RuneCountInString(s string) (n int)",
        #   "doc": "RuneCountInString is like RuneCount but its input is a string.\n",
        #   "pos": "/usr/local/Cellar/go/1.9/libexec/src/unicode/utf8/utf8.go:412:6"
        # }

        doc_body = StringIO()
        link = ""

        try:
            doc_map = json.loads(doc_result)

            doc_import = doc_map.get("import", "")
            doc_signature = doc_map.get("decl", "")
            doc_string = doc_map.get("doc", "")
            link = doc_map.get("pos", "")

            if doc_import:
                doc_body.write(
                    "<em>package: <strong>%s</strong></em>" % escape(doc_import)
                )

            if doc_signature:
                doc_body.write(
                    "<p><strong>%s</strong></p>"
                    % cls.translate_space(escape(doc_signature))
                )

            if doc_string:
                doc_body.write("<p>%s</p>" % cls.translate_space(escape(doc_string)))

            if link:
                doc_body.write("<a href='%s'>Go to definition</a>" % escape(link))

        except json.JSONDecodeError:
            pass

        finally:
            logger.debug(doc_body.getvalue())
            return cls(doc=doc_body.getvalue())


def get_completion(source: str, file_path: str, location: int):
    gocode = Gocode(source, file_path)
    completions = tuple(gocode.complete(location))
    return completions


def get_documentation(source: str, file_path: str, location: int):
    gogetdoc = Gogetdoc(source, file_path)
    return Documentation.from_gogetdocresult(
        gogetdoc.get_documentation(location)
    ).to_html()


def get_formatted_code(source: str, file_path: str):

    # default use gofmt
    command = ["gofmt"]
    env = os.environ.copy()
    workdir = os.path.dirname(file_path)

    # use goimports if available
    gopath = env.get("GOPATH")
    executable = "goimports.exe" if os.name == "nt" else "goimports"
    if os.path.isfile(os.path.join(gopath, "bin", executable)):
        command = ["goimports"]

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
