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
import itertools
import os
import queue
import threading

import sublime, sublime_plugin


from .core.api import (
    get_completion,
    get_documentation,
    get_formatted_code,
    get_diagnostic,
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

        status_key = "gotools"
        value = "BUSY"
        view = sublime.active_window().active_view()
        view.set_status(status_key, value)
        with PROCESS_LOCK:
            function = func(*args, **kwargs)
            view.erase_status(status_key)
            return function

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
        # logger.debug(completions)
        return cls(completions)


class CompletionsCacheItem:
    def __init__(self, path, source, completions):
        self.source_path = path
        self.source = source
        self._completions = completions

    @property
    def data(self):
        if not self._completions:
            return ()
        return self._completions


class DocumentationCacheItem:
    def __init__(self, path, source, documentation):
        self.source_path = path
        self.source = source
        self._documentation = documentation

    @property
    def data(self):
        if not self._documentation:
            return ""
        return self._documentation


class Cache:
    def __init__(self, item_class, *, max_cache=50):
        self.item_class = item_class
        self.max_cache = max_cache
        self.data = ()

    def set(self, path, source, data):
        item = self.item_class(path, source, data)
        self.data = tuple(itertools.chain(self.data[: self.max_cache], (item,)))

    def get(self, path, source):

        logger.debug("cached = : %s", len(self.data))
        if not self.data:
            return None

        index = len(self.data) - 1
        while index > -1:
            c = self.data[index]
            if c.source_path == path and c.source == source:
                return c.data

            index -= 1

        # if no result available
        return None


COMPLETIONS_CACHE = Cache(CompletionsCacheItem, max_cache=25)
DOCUMENTATION_CACHE = Cache(DocumentationCacheItem)


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


class CompletionParams:
    def __init__(self, view: sublime.View):

        self.source = view.substr(sublime.Region(0, view.size()))
        self.location = view.sel()[0].a
        self.file_name = view.file_name()

        prefix = view.word(self.location)
        prefix_str = view.substr(prefix).strip("\n")
        logger.debug("prefix_str: %s", repr(prefix_str))
        if prefix_str.isidentifier():
            self.location = prefix.a


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

    completions_queue = queue.Queue(1)

    def __init__(self, view: sublime.View):
        self.view = view
        self.completions_pos = -1

    @process_lock
    def completion_thread(self, view: sublime.View, cparams: CompletionParams):
        with COMPLETION_LOCK:

            source = cparams.source
            location = cparams.location
            file_path = cparams.file_name

            cached = COMPLETIONS_CACHE.get(file_path, source[:location])
            if cached:
                logger.debug("using cached")
                raw_completions = cached

            else:
                raw_completions = get_completion(source, file_path, location)
                COMPLETIONS_CACHE.set(file_path, source[:location], raw_completions)

            completion = Completion.from_gocoderesult(raw_completions)
            try:
                self.completions_queue.put_nowait(completion.to_sublime())
            except queue.Full:
                pass

        show_completions(view)

    def on_query_completions(self, prefix: str, locations):
        if not PLUGIN_ENABLED:
            return None

        if not valid_source(self.view):
            return None

        if not valid_scope(self.view, locations[0]):
            return ((), sublime.INHIBIT_EXPLICIT_COMPLETIONS)

        if COMPLETION_LOCK.locked():
            self.view.run_command("hide_auto_complete")
            return None

        try:
            completions = self.completions_queue.get_nowait()
        except queue.Empty:
            completions = None

        completion_params = CompletionParams(self.view)

        if completions:
            if completion_params.location != self.completions_pos:
                logger.debug(
                    "invalid context: request post = %d, expected = %s",
                    self.completions_pos,
                    completion_params.location,
                )
                self.view.run_command("hide_auto_complete")
                return None

            return completions

        self.completions_pos = completion_params.location

        logger.debug("prefix = '%s'", prefix)
        logger.debug("request pos = '%d'", self.completions_pos)

        thread = threading.Thread(
            target=self.completion_thread, args=(self.view, completion_params)
        )
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
    def get_documentation(self, view: sublime.View, location: int):
        sel_word = view.word(location)
        offset = sel_word.a
        source = view.substr(sublime.Region(0, view.size()))
        file_path = view.file_name()

        popup_location = location
        popup_content = ""

        cached = DOCUMENTATION_CACHE.get(file_path, source[: sel_word.b])
        if cached:
            logger.debug("using cached")
            popup_content = cached

        else:
            documentation = get_documentation(source, file_path, offset)
            DOCUMENTATION_CACHE.set(file_path, source[: sel_word.b], documentation)
            popup_content = documentation

        def open_file(file_name):
            view.window().open_file(file_name, sublime.ENCODED_POSITION)

        if popup_content:

            show_popup(
                view,
                content=popup_content,
                location=popup_location,
                on_navigate=open_file,
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

        thread = threading.Thread(target=self.do_formatting, args=(view,))
        thread.start()

    @process_lock
    def do_formatting(self, view):
        logger.info("formatting thread")

        file_name = view.file_name()
        source = view.substr(sublime.Region(0, view.size()))

        try:
            formatted = get_formatted_code(source, file_name)

        except Exception as err:
            self.show_error_panel(
                view.window(), str(err).replace("<standard input>", file_name),
            )

        else:
            output_panel = ErrorPanel(view.window())
            output_panel.destroy()

            if not formatted:
                return

            nview = view.window().open_file(file_name)

            nview.run_command(
                "gotools_apply_changes",
                args={"file_name": file_name, "new_source": formatted},
            )

    @staticmethod
    def show_error_panel(window: sublime.Window, message: str):
        """show error in output panel"""

        output_panel = ErrorPanel(window)
        output_panel.append(message)
        output_panel.show()

    def is_visible(self):
        return valid_source(self.view)


class GotoolsApplyChangesCommand(sublime_plugin.TextCommand):
    """document apply changes command"""

    def run(self, edit, file_name, new_source):
        logger.debug("new_source:-------\n%s", new_source)

        view = self.view
        if file_name != self.view.file_name():
            raise ValueError("unable apply change for %s", file_name)

        old = view.substr(sublime.Region(0, view.size()))
        self.apply_changes(view, edit, old, new_source)

    def apply_changes(self, view, edit, old, new):
        """apply formatting changes"""

        i = 0
        for line in difflib.ndiff(old.splitlines(), new.splitlines()):

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


class GotoolsVetFileCommand(sublime_plugin.TextCommand):
    """document vet file command"""

    def run(self, edit):
        view = self.view
        view.run_command("gotools_vet", {"path": view.file_name()})

    def is_visible(self):
        return valid_source(self.view)


class GotoolsVetCommand(sublime_plugin.TextCommand):
    """document vet directory command"""

    def run(self, edit, path=""):
        if not PLUGIN_ENABLED:
            return

        view = self.view

        if not valid_source(view):
            return

        if not path:
            path = os.path.dirname(view.file_name())

        thread = threading.Thread(target=self.diagnostic_thread, args=(view, path))
        thread.start()

    @process_lock
    def diagnostic_thread(self, view: sublime.View, path: str):

        work_dir = path
        if os.path.isfile(path):
            work_dir = os.path.dirname(path)

        diagnostic = get_diagnostic(path, workdir=work_dir)
        logger.debug(diagnostic)

        output_panel = DiagnosticPanel(self.view.window())
        output_panel.append(diagnostic)
        output_panel.show()

    def is_visible(self):
        return valid_source(self.view)
