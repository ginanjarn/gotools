import logging

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
stream_formatter = "%(levelname)s %(asctime)s: %(filename)s:%(lineno)s:  %(message)s"
sh.setFormatter(logging.Formatter(stream_formatter))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)

import difflib
import os
import threading
from collections import namedtuple

import sublime, sublime_plugin


from .core.api import (
    get_completion,
    get_documentation,
    get_formatted_code,
    get_diagnostic,
)

from .core.sublime_text import show_completions, show_popup, OutputPanel


GocodeResult = namedtuple("GocodeResult", ["type_", "name", "data", "package"])


class Completion:
    """completion halder"""

    def __init__(self, completions):
        self.completions = completions

    def to_sublime(self):
        return (
            self.completions,
            sublime.INHIBIT_WORD_COMPLETIONS,
        )

    @staticmethod
    def transform_type(type_: str):
        type_map = {
            "func": sublime.KIND_FUNCTION,
            "package": sublime.KIND_NAMESPACE,
            "type": sublime.KIND_TYPE,
            "const": sublime.KIND_NAVIGATION,
            "var": sublime.KIND_VARIABLE,
            # "PANIC": sublime.KIND_NAVIGATION, # gocode error
        }
        return type_map.get(type_, sublime.KIND_AMBIGUOUS)

    @classmethod
    def from_gocode_csv(cls, raw: str):
        """parse from gocode csv"""

        logger.debug(raw)
        completions = []
        for line in raw.splitlines():
            raw = GocodeResult(*line.split(",,"))
            annotation = (
                "%s%s" % (raw.name, raw.data[4:]) if raw.type_ == "func" else raw.data
            )
            details = "<strong>%s%s</strong>" % (
                "" if raw.type_ == "package" else "%s." % raw.package,
                raw.name,
            )
            completions.append(
                sublime.CompletionItem(
                    trigger=raw.name,
                    annotation=annotation,
                    kind=cls.transform_type(raw.type_),
                    details=details,
                )
            )

        return cls(completions)


class Documentation:
    def __init__(self, doc: str, *, package: str = "", methodOrField: str = ""):
        self.documentation = doc
        self.pkg_method = "%s%s" % (
            package,
            "" if not methodOrField else "%s" % methodOrField,
        )

    @classmethod
    def from_gocode_csv(cls, raw: str):
        """parse from gocode csv"""

        candidates = raw.splitlines()
        result = GocodeResult(*candidates[0].split(",,"))
        logger.debug(result)

        if result.name == "main":
            return cls(doc="")

        if result.data == "invalid type":
            return cls(doc="")

        doc_template = "<body style='padding: 0.5em;'><strong>{head}</strong><div><p>{body}</p><a href='#'>More</a></div></body>"

        if result.type_ == "package":
            doc = doc_template.format(
                head=result.name, body="package %s" % (result.name)
            )
            return cls(doc, package=result.name)

        if result.type_ == "func":
            head = "%s.%s" % (result.package, result.name)
            body = "%s%s" % (result.name, result.data[4:])
            doc = doc_template.format(head=head, body=body)
            return cls(doc, package=result.package, methodOrField=result.name)

        head = "%s.%s" % (result.package, result.name)
        body = "%s %s %s" % (result.type_, result.name, result.data)
        doc = doc_template.format(head=head, body=body)
        return cls(doc, package=result.package, methodOrField=result.name)


def valid_source(view: sublime.View, location: int = 0) -> bool:
    """valid go source code"""
    return view.match_selector(location, "source.go")


def valid_scope(view: sublime.View, location: int) -> bool:
    """valid scope for completion"""

    if view.match_selector(location, "source.go string"):
        return False

    if view.match_selector(location, "source.go comment"):
        return False

    return True


PLUGIN_ENABLED = False


def enable_plugin(enable=True):
    global PLUGIN_ENABLED

    PLUGIN_ENABLED = enable


class Event(sublime_plugin.ViewEventListener):
    """Event handler"""

    def __init__(self, view: sublime.View):
        self.view = view
        self.completions = None

    def completion_thread(self, view: sublime.View):
        source = view.substr(sublime.Region(0, view.size()))
        location = view.sel()[0].a
        workdir = os.path.dirname(view.file_name())

        raw_completions = get_completion(source, workdir, location)
        completion = Completion.from_gocode_csv(raw_completions)

        self.completions = completion.to_sublime()

        show_completions(view)

    def on_query_completions(self, prefix: str, locations):
        if not PLUGIN_ENABLED:
            return None

        if not valid_scope(self.view, locations[0]):
            return ([], sublime.INHIBIT_EXPLICIT_COMPLETIONS)

        if self.completions:
            completions = self.completions
            self.completions = None
            return completions

        thread = threading.Thread(target=self.completion_thread, args=(self.view,))
        thread.start()
        return None

    def on_activated(self):
        if valid_source(self.view):
            enable_plugin()
        else:
            enable_plugin(False)

    def on_post_save(self):
        if valid_source(self.view):
            enable_plugin()
        else:
            enable_plugin(False)

    def get_documentation(self, view: sublime.View, location: int):
        end = view.word(location).b
        source = view.substr(sublime.Region(0, view.size()))
        workdir = os.path.dirname(view.file_name())
        candidates = get_documentation(source, workdir, end)
        if not candidates:
            return

        doc = Documentation.from_gocode_csv(candidates)

        if doc.documentation:
            show_popup(view, content=doc.documentation, location=location)

    def on_hover(self, point: int, hover_zone: int):
        if not PLUGIN_ENABLED:
            return

        if not valid_scope(self.view, point):
            return

        if hover_zone == sublime.HOVER_TEXT:
            thread = threading.Thread(
                target=self.get_documentation, args=(self.view, point)
            )
            thread.start()


class GotoolsFormatCommand(sublime_plugin.TextCommand):
    """document formatter command"""

    def run(self, edit):
        if not PLUGIN_ENABLED:
            return

        view = self.view

        if not valid_source(view):
            return

        source = view.substr(sublime.Region(0, view.size()))
        formatted = get_formatted_code(source)

        i = 0
        for line in difflib.ndiff(source.splitlines(), formatted.splitlines()):

            if line.startswith("?"):  # skip hint lines
                continue

            l = (len(line) - 2) + 1

            if line.startswith("-"):
                self.diff_sanity_check(
                    view.substr(sublime.Region(i, i + l - 1)), line[2:]
                )
                view.erase(edit, sublime.Region(i, i + l))

            elif line.startswith("+"):
                view.insert(edit, i, "%s\n" % (line[2:]))
                i += l

            else:
                self.diff_sanity_check(
                    view.substr(sublime.Region(i, i + l - 1)), line[2:]
                )
                i += l

    @staticmethod
    def diff_sanity_check(a, b):
        if a != b:
            raise Exception("diff sanity check mismatch\n-%s\n+%s" % (a, b))

    def is_visible(self):
        return valid_source(self.view)


class GotoolsValidateCommand(sublime_plugin.TextCommand):
    """document formatter command"""

    def run(self, edit):
        if not PLUGIN_ENABLED:
            return

        view = self.view

        if not valid_source(view):
            return

        thread = threading.Thread(target=self.diagnostic_thread, args=(view,))
        thread.start()

    def diagnostic_thread(self, view: sublime.View):

        file_name = view.file_name()
        work_dir = os.path.dirname(file_name)

        for folder in view.window().folders():
            if file_name.startswith(folder):
                work_dir = folder

        diagnostic = get_diagnostic(view.file_name(), workdir=work_dir)
        logger.debug(diagnostic)

        output_panel = OutputPanel(self.view.window(), "gotools")
        output_panel.append(diagnostic)
        output_panel.show()

    def is_visible(self):
        return valid_source(self.view)
