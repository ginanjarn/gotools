"""plugin module"""


from .sublimetext.core import complete  # , fetch_documentation
from .sublimetext.core import get_definition, get_documentation, build_documentation
from .sublimetext.core import format_code
from .sublimetext.core import CompletionError, DocumentationError, FormattingError
from .sublimetext.view import show_completions, show_popup, open_link
from .sublimetext.document import apply_changes
import threading
import sublime
import sublime_plugin
import os
import re
import difflib
import logging

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(levelname)s\t%(module)s: %(lineno)d\t%(message)s"))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)


def valid_source(view, point=0):
    """check if valid source go"""
    return view.match_selector(point, "source.go")


def valid_attribute(view, point):
    """check if valid attribute go"""

    return all(
        [
            not view.match_selector(point, "string"),
            not view.match_selector(point, "comment"),
        ]
    )


def build_signature(completion: dict) -> str:
    base_signature = completion["signature"]
    if completion["type"] == "func":
        return "".join([completion["name"] + base_signature[4:]])
    return base_signature


def extract_arguments(arguments: str) -> "Iterator[str]":
    found = re.findall(r"\w+\(([\w\s\,\.\{\}]*)\).*", arguments)
    if any(found):
        yield from (args for args in found[0].split(","))


def build_func_result(base_name: str, base_signature: str) -> str:
    signature = ",".join(
        [
            "${%s:%s}" % val
            for val in enumerate(extract_arguments(base_signature), start=1)
        ]
    )
    return "%s(%s)" % (base_name, signature)


def build_completion_result(completion: dict) -> str:
    base_name = completion["name"]
    base_signature = completion["signature"]
    if completion["type"] == "func":
        return build_func_result(base_name, base_signature)
    return base_name


def build_completion(completions):
    """build completion"""

    for completion in completions:
        # yield (completion["type"], completion["name"],completion["signature"],completion["module"])
        yield (
            "{name}  \t{signature}".format(
                name=completion["name"], signature=build_signature(completion)
            ),
            build_completion_result(completion),
        )


class GoTools(sublime_plugin.EventListener):
    """Event based command"""

    def __init__(self):
        self.completion = None

        # completion cache
        self.cached_completion = None
        self.cached_source = None

        # documentation cache
        self.cached_documentation = {}

    def fetch_completion(self, view, prefix, locations):
        """fetch completion"""

        offset = locations[0]
        word_region = view.word(offset)
        if view.substr(word_region).isidentifier():
            offset = word_region.a

        temp_source = view.substr(sublime.Region(0, offset))
        if temp_source == self.cached_source:
            self.completion = self.cached_completion
            show_completions(view)
        else:
            self.cached_source = temp_source
            try:
                results = complete(
                    source=view.substr(sublime.Region(0, view.size())),
                    offset=offset,
                    workdir=os.path.dirname(view.file_name()),
                )
            except CompletionError:
                logger.error("completion error", exc_info=True)
                return
            else:
                completion = (
                    list(build_completion(results)),
                    sublime.INHIBIT_EXPLICIT_COMPLETIONS
                    | sublime.INHIBIT_WORD_COMPLETIONS,
                )
                self.completion = completion
                logger.debug(self.completion)
                self.cached_completion = completion
                show_completions(view)

    def on_query_completions(self, view, prefix, locations):
        """on query completion listener"""

        if not all([valid_source(view), valid_attribute(view, locations[0])]):
            return

        if self.completion:
            completion = self.completion
            self.completion = None
            return completion

        thread = threading.Thread(
            target=self.fetch_completion, args=(view, prefix, locations)
        )
        thread.start()

    def fetch_documentation(self, view, point):

        source = view.substr(sublime.Region(0, view.size()))
        file_name = view.file_name()
        attribute = view.substr(view.word(point))
        try:
            definition = get_definition(source, point, file_name=file_name)

            def_path = definition["path"]
            symbol = (
                "%s.%s" % (os.path.dirname(def_path), attribute)
                if def_path != file_name
                else None
            )

            doc = None
            if symbol:
                base_symbol = os.path.basename(symbol)
                if base_symbol in self.cached_documentation:
                    logger.debug("use cached")
                    doc = self.cached_documentation[base_symbol]
                else:
                    doc = get_documentation(symbol)
                    self.cached_documentation[base_symbol] = doc

            result = build_documentation(definition, message=doc)

        except DocumentationError:
            logger.error("documentation error", exc_info=True)
        else:
            content = "<div style='padding:0.5em'>%s</div>" % result["content"].replace(
                "\t", "&nbsp;&nbsp;"
            ).replace("  ", "&nbsp;")
            logger.debug(content)

            show_popup(view, content, point, lambda _: open_link(view, result["link"]))

    def on_hover(self, view, point, hover_zone):
        """on hover listener"""

        if all(
            [
                valid_source(view),
                valid_attribute(view, point),
                hover_zone == sublime.HOVER_TEXT,
            ]
        ):
            thread = threading.Thread(
                target=self.fetch_documentation, args=(view, point)
            )
            thread.start()


class GotoolsFormatCommand(sublime_plugin.TextCommand):
    """Formatting command"""

    def run(self, edit):
        """run command"""

        view = self.view
        try:
            source = view.substr(sublime.Region(0, view.size()))
            result = format_code(source)
        except FormattingError:
            logger.error("formatting error")
        else:
            apply_changes(view, edit, result)
