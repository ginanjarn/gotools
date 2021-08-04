import logging

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
stream_formatter = "%(levelname)s %(asctime)s: %(filename)s:%(lineno)s:  %(message)s"
sh.setFormatter(logging.Formatter(stream_formatter))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)

from functools import wraps
import difflib
import os
import re
import threading

import sublime, sublime_plugin


from .core.api import (
    get_completion,
    get_documentation,
    get_formatted_code,
    get_diagnostic,
    get_godoc_documentation,
)

from .core.sublime_text import (
    show_completions,
    hide_completions,
    show_popup,
    DiagnosticPanel,
    ErrorPanel,
)

PROCESS_LOCK = threading.Lock()


def process_lock(func):
    """process pipeline. single process allowed"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if PROCESS_LOCK.locked():
            return None

        with PROCESS_LOCK:
            return func(*args, **kwargs)

    return wrapper


class Completion:
    """completion halder"""

    def __init__(self, completions):
        self.completions = tuple(completions)

    def to_sublime(self):
        return (
            self.completions,
            sublime.INHIBIT_WORD_COMPLETIONS | sublime.INHIBIT_EXPLICIT_COMPLETIONS,
        )

    @staticmethod
    def transform_type(type_: str):
        type_map = {
            "keyword": sublime.KIND_KEYWORD,
            "func": sublime.KIND_FUNCTION,
            "package": sublime.KIND_NAMESPACE,
            "type": sublime.KIND_TYPE,
            "const": sublime.KIND_NAVIGATION,
            "var": sublime.KIND_VARIABLE,
            # "PANIC": sublime.KIND_NAVIGATION, # gocode error
        }
        return type_map.get(type_, sublime.KIND_AMBIGUOUS)

    @classmethod
    def from_gocoderesult(cls, gocode_results):
        completions = []
        for raw in gocode_results:
            annotation = (
                "%s%s" % (raw.name, raw.data[4:]) if raw.type_ == "func" else raw.data
            )
            details = "<strong>%s%s</strong>" % (
                "%s." % raw.package if raw.package else "",
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
        completions.sort(key=lambda c: c.trigger)
        logger.debug(completions)
        return cls(completions)


class CompletionContextMatcher:
    """context matcher"""

    def __init__(self, completions):
        self.completions = completions

    def _filter_type(self):
        yield from (
            completion for completion in self.completions if completion.type_ == "type"
        )

    def _filter_package(self, name: str):
        yield from (
            completion for completion in self.completions if completion.package == name
        )

    def get_matched(self, line_str: str):
        logger.debug("to match: %s", line_str)

        matched = re.match(r".*(?:var|const)(?:\s+\w+)(\s*\w*)$", line_str)
        if matched:
            return tuple(self._filter_type())

        return self.completions


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


def plugin_loaded():
    settings_basename = "Go.sublime-settings"
    sublime_settings = sublime.load_settings(settings_basename)
    sublime_settings.set("index_files", False)
    sublime_settings.set("auto_complete_use_index", False)
    sublime_settings.set("show_definitions", False)
    sublime.save_settings(settings_basename)

    enable_plugin()


COMPLETION_LOCK = threading.Lock()


class Event(sublime_plugin.ViewEventListener):
    """Event handler"""

    def __init__(self, view: sublime.View):
        self.view = view
        self.completions = None
        self.context_pos = 0

    def cancel_completion(self, view: sublime.View, location: int):
        line_region = view.line(location)
        line_str = view.substr(sublime.Region(line_region.a, location))

        logger.debug("compare line: %s", line_str)

        matched = re.match(r".*(?:const|type|var)(\s*\w*)$", line_str,)
        if matched:
            return True

        matched = re.match(
            r".*(?:break|continue|func|import|interface|package|struct)(\s*\w*)*$",
            line_str,
        )
        if matched:
            return True

        return False

    @process_lock
    def completion_thread(self, view: sublime.View):
        with COMPLETION_LOCK:
            source = view.substr(sublime.Region(0, view.size()))
            location = view.sel()[0].a
            file_path = view.file_name()

            raw_completions = get_completion(source, file_path, location)

            line_region = view.line(location)
            line_str = view.substr(sublime.Region(line_region.a, location))

            cm = CompletionContextMatcher(raw_completions)
            raw_completions = cm.get_matched(line_str)

            completion = Completion.from_gocoderesult(raw_completions)
            self.completions = completion.to_sublime()

        show_completions(view)

    def on_query_completions(self, prefix: str, locations):
        if not PLUGIN_ENABLED:
            return None

        if not valid_source(self.view):
            return None

        if not valid_scope(self.view, locations[0]):
            return ((), sublime.INHIBIT_EXPLICIT_COMPLETIONS)

        if self.cancel_completion(self.view, locations[0]):
            logger.debug("canceled")
            hide_completions(self.view)
            return None

        if COMPLETION_LOCK.locked():
            self.view.run_command("hide_auto_complete")
            return None

        context_pos = 0
        if str.isidentifier(prefix):
            context_pos = self.view.word(locations[0]).a
        else:
            context_pos = locations[0]

        if self.context_pos != context_pos:
            self.completions = None
            self.view.run_command("hide_auto_complete")

        self.context_pos = context_pos

        if self.completions:
            completions = self.completions
            self.completions = None
            return completions

        logger.debug("prefix = '%s'", prefix)

        thread = threading.Thread(target=self.completion_thread, args=(self.view,))
        thread.start()
        hide_completions(self.view)
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

    def on_modified(self):
        if not PLUGIN_ENABLED:
            return

        view = self.view
        if view.is_auto_complete_visible():
            word = view.substr(view.word(view.sel()[0].a)).strip()
            if not str.isidentifier(word):
                view.run_command("hide_auto_complete")

    @process_lock
    def get_godoc_thread(self, methodOrField):
        view = self.view
        view.update_popup(self.popup_content + "<br>loading . . .<br>")

        workdir = os.path.dirname(view.file_name())
        content = get_godoc_documentation(methodOrField, workdir)

        if content:
            show_popup(
                view, content=content, location=self.popup_location,
            )
        else:
            view.update_popup(self.popup_content)

    def get_godoc_documentation(self, methodOrField):
        thread = threading.Thread(target=self.get_godoc_thread, args=(methodOrField,))
        thread.start()

    @process_lock
    def get_documentation(self, view: sublime.View, location: int):
        end = view.word(location).b
        source = view.substr(sublime.Region(0, view.size()))
        file_path = view.file_name()

        documentation = get_documentation(source, file_path, end)
        self.popup_location = location
        self.popup_content = documentation

        if documentation:
            show_popup(
                view,
                content=self.popup_content,
                location=self.popup_location,
                on_navigate=self.get_godoc_documentation,
            )

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

        try:
            formatted = get_formatted_code(source)

        except Exception as err:
            file_name = os.path.basename(view.file_name())

            self.show_error_panel(
                view.window(), str(err).replace("<standard input>", file_name),
            )

        else:
            output_panel = ErrorPanel(view.window())
            output_panel.destroy()

            if not formatted:
                return

            self.apply_changes(view, edit, source, formatted)

    def apply_changes(self, view, edit, source, formatted):
        """apply formatting changes"""

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
    def show_error_panel(window: sublime.Window, message: str):
        """show error in output panel"""

        output_panel = ErrorPanel(window)
        output_panel.append(message)
        output_panel.show()

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

        output_panel = DiagnosticPanel(self.view.window())
        output_panel.append(diagnostic)
        output_panel.show()

    def is_visible(self):
        return valid_source(self.view)
