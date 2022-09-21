"""gotools main"""

import itertools
import logging
import os
import re
import threading
import time

from functools import wraps
from io import StringIO
from pathlib import Path
from typing import List, Iterator, Optional

import sublime
import sublime_plugin
from .third_party import mistune

from .api import lsp
from .api import tools


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)


class StatusMessage:
    """handle status message"""

    status_key = "gotools_status"

    def set_status(self, message: str):
        view: sublime.View = sublime.active_window().active_view()
        view.set_status(self.status_key, f"🔄 {message}")

    def erase_status(self):
        view: sublime.View = sublime.active_window().active_view()
        view.erase_status(self.status_key)

    def show_message(self, message: str):
        window: sublime.Window = sublime.active_window()
        window.status_message(message)


STATUS_MESSAGE: StatusMessage = None


class TextChangeItem:
    """Text change item"""

    __slots__ = ["region", "text", "offset_move"]

    def __init__(self, region: sublime.Region, text: str, offset_move: int = 0):
        self.region = region
        self.text = text
        self.offset_move = offset_move

    def __repr__(self):
        return f"TextChangeItem({repr(self.region)},{repr(self.text)},{repr(self.offset_move)})"

    def get_region(self, offset_move: int, /) -> sublime.Region:
        """get region adapted with cursor movement"""
        return sublime.Region(self.region.a + offset_move, self.region.b + offset_move)

    @classmethod
    def from_rpc(cls, view: sublime.View, change: dict, /):
        start = change["range"]["start"]
        end = change["range"]["end"]
        new_text = change["newText"]

        start_point = view.text_point_utf16(start["line"], start["character"])
        end_point = view.text_point_utf16(end["line"], end["character"])

        region = sublime.Region(start_point, end_point)
        move = len(new_text) - region.size()
        return cls(region, new_text, move)


TEXT_CHANGE_PROCESS = threading.Lock()
TEXT_CHANGE_SYNC = threading.Event()


class GotoolsApplyTextChangesCommand(sublime_plugin.TextCommand):
    """apply text changes"""

    def run(self, edit: sublime.Edit, text_changes: List[dict]):
        LOGGER.debug(f"apply_text_changes: {text_changes}")
        if not text_changes:
            return

        try:
            text_changes = [
                TextChangeItem.from_rpc(self.view, change) for change in text_changes
            ]
        except Exception as err:
            LOGGER.error(err, exc_info=True)
        else:
            self.apply_changes(edit, text_changes)
        finally:
            TEXT_CHANGE_SYNC.set()

    def apply_changes(self, edit: sublime.Edit, changes: List[TextChangeItem]):
        """apply text changes"""

        move = 0
        for change in changes:
            region = change.get_region(move)
            self.view.erase(edit, region)
            self.view.insert(edit, region.a, change.text)
            move += change.offset_move


class UnbufferedDocument:
    """unbuffered document handler"""

    def __init__(self, file_name: str):
        self.file_name = file_name
        self.buffer: StringIO = StringIO()

    def apply_text_changes(self, text_changes: List[dict]):
        try:
            # load
            with open(self.file_name, "r") as file:
                self.buffer = StringIO(file.read())

            # apply
            for change in text_changes:
                self._apply_text_change(change)

            # save
            with open(self.file_name, "w") as file:
                file.write(self.buffer.getvalue())
        finally:
            TEXT_CHANGE_SYNC.set()

    def _apply_text_change(self, change):
        start = change["range"]["start"]
        end = change["range"]["end"]
        new_text = change["newText"]

        start_line, start_character = start["line"], start["character"]
        end_line, end_character = end["line"], end["character"]

        new_buf = StringIO()
        for index, line in enumerate(self.buffer.readlines()):

            if index < start_line:
                new_buf.write(line)
                continue

            if index > end_line:
                new_buf.write(line)
                continue

            if index == start_line:
                new_buf.write(line[:start_character])
                new_buf.write(new_text)

            if index == end_line:
                new_buf.write(line[end_character:])

        self.buffer = new_buf


class GotoolsApplyCompletionCommand(sublime_plugin.TextCommand):
    """apply completion"""

    def run(
        self, edit: sublime.Edit, text_edit: dict, additional_text_edit: List[dict]
    ):
        # gopls apply additional changes after completion applied
        self.view.run_command(
            "gotools_apply_text_changes", {"text_changes": [text_edit]}
        )
        self.view.run_command(
            "gotools_apply_text_changes", {"text_changes": additional_text_edit}
        )


# custom kind
KIND_PATH = (sublime.KIND_ID_NAVIGATION, "p", "")
KIND_VALUE = (sublime.KIND_ID_NAVIGATION, "u", "")


class CompletionItem(sublime.CompletionItem):
    """completion item"""

    KIND_MAP = {
        1: sublime.KIND_NAVIGATION,
        2: sublime.KIND_FUNCTION,
        3: sublime.KIND_FUNCTION,
        4: sublime.KIND_FUNCTION,
        5: sublime.KIND_VARIABLE,
        6: sublime.KIND_VARIABLE,
        7: sublime.KIND_TYPE,
        8: sublime.KIND_TYPE,
        9: sublime.KIND_NAMESPACE,
        10: sublime.KIND_VARIABLE,
        11: KIND_VALUE,
        12: KIND_VALUE,
        13: sublime.KIND_TYPE,
        14: sublime.KIND_KEYWORD,
        15: sublime.KIND_SNIPPET,
        16: KIND_VALUE,
        17: KIND_PATH,
        18: sublime.KIND_NAVIGATION,
        19: KIND_PATH,
        20: sublime.KIND_VARIABLE,
        21: sublime.KIND_VARIABLE,
        22: sublime.KIND_TYPE,
        23: sublime.KIND_AMBIGUOUS,
        24: sublime.KIND_MARKUP,
        25: sublime.KIND_TYPE,
    }

    @classmethod
    def from_rpc(cls, item: dict):
        """create from rpc"""

        label = item["label"]
        filter_text = item.get("filterText", label)
        kind = item["kind"]
        annotation = item.get("detail", "")

        text_edit = item["textEdit"]
        additional_text_edit = item.get("additionalTextEdits", [])

        # sublime remove prefix completion
        text_edit["range"]["end"] = text_edit["range"]["start"]

        return cls.command_completion(
            trigger=filter_text,
            command="gotools_apply_completion",
            args={"text_edit": text_edit, "additional_text_edit": additional_text_edit},
            kind=cls.KIND_MAP.get(kind, sublime.KIND_AMBIGUOUS),
            annotation=annotation,
        )


class ViewNotFoundError(ValueError):
    """view not found in buffer"""


class DocumentNotFound(ValueError):
    """document not found in buffer"""


class BufferedDocument:
    """buffered document handler"""

    def __init__(self, file_name: str):
        self.view: sublime.View = sublime.active_window().find_open_file(file_name)
        if not self.view:
            raise ViewNotFoundError(f"{repr(file_name)} not found in buffer")

        self._cached_completions: List[CompletionItem] = None
        self.version = 0
        self.diagnostics = {}

    @classmethod
    def from_active_view(cls):
        file_name = sublime.active_window().active_view().file_name()
        return cls(file_name)

    def increment_version(self):
        self.version += 1

    def save(self):
        self.view.run_command("save")

    def get_cached_completion(self) -> List[CompletionItem]:
        completions = self._cached_completions
        self._cached_completions = None
        return completions

    def file_name(self) -> str:
        return self.view.file_name()

    def source(self) -> str:
        region = sublime.Region(0, self.view.size())
        return self.view.substr(region)

    def show_completion(self, completion: dict):
        if completions := completion.get("items"):
            try:
                items = [CompletionItem.from_rpc(item) for item in completions]
            except Exception as err:
                LOGGER.error(err, exc_info=True)
            else:
                self._cached_completions = items
                self.trigger_completion()

    def trigger_completion(self):
        self.view.run_command(
            "auto_complete",
            {
                "disable_auto_insert": True,
                "next_completion_if_showing": False,
                "auto_complete_commit_on_tab": True,
            },
        )

    def hide_completion(self):
        self.view.run_command("hide_auto_complete")

    _start_pre_code_pattern = re.compile(r"^<pre><code.*>")
    _end_pre_code_pattern = re.compile(r"</code></pre>$")

    @staticmethod
    def adapt_minihtml(lines: str) -> Iterator[str]:
        """adapt sublime minihtml tag

        Not all html tag implemented
        """
        pre_tag = False
        for line in lines.splitlines():

            if open_pre := BufferedDocument._start_pre_code_pattern.match(line):
                line = "<div class='code_block'>%s" % line[open_pre.end() :]
                pre_tag = True

            if closing_pre := BufferedDocument._end_pre_code_pattern.search(line):
                line = "%s</div>" % line[: closing_pre.start()]
                pre_tag = False

            line = line.replace("  ", "&nbsp;&nbsp;")
            line = f"{line}<br />" if pre_tag else line

            yield line

    def show_documentation(self, documentation: dict):
        def show_popup(text, location):
            self.view.show_popup(
                content=text,
                location=location,
                max_width=720,
                flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            )

        style = """
        <style>
        body { margin: 0.8em; font-family: BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }
        code, .code_block {
            background-color: color(var(--background) alpha(0.8));
            font-family: monospace;
            border-radius: 0.4em;
        }
        code { padding: 0 0.2em 0 0.2em; }
        .code_block { padding: 0.4em; }
        ol, ul { padding-left: 1em; }
        .footer {
            padding: 0.4em;
            background-color: color(var(--background) alpha(0.8));
        }
        </style>
        """

        def create_footer(point):
            href = f'subl:gotools_goto_definition {{"point":{point}}}'
            return f"<div class='footer'><a href='{href}'>Go to definition</a></div>"

        if contents := documentation.get("contents"):
            line = documentation["range"]["start"]["line"]
            character = documentation["range"]["start"]["character"]
            point = self.view.text_point_utf16(line, character)
            kind = contents.get("kind")
            value = contents["value"]
            footer = create_footer(point)

            value = (
                mistune.markdown(value, escape=False) if kind == "markdown" else value
            )
            value = "\n".join(self.adapt_minihtml(value))

            show_popup("".join([style, value, footer]), point)

    def apply_text_changes(self, changes: List[dict]):
        self.view.run_command("gotools_apply_text_changes", {"text_changes": changes})

    def get_get_highligth_regions(self) -> List[sublime.Region]:

        if not self.diagnostics:
            return []

        def get_region(diagnostic) -> sublime.Region:
            view = self.view

            start = diagnostic["range"]["start"]
            end = diagnostic["range"]["end"]
            start_point = view.text_point_utf16(start["line"], start["character"])
            end_point = view.text_point_utf16(end["line"], end["character"])

            if start_point == end_point:
                return view.line(start_point)

            return sublime.Region(start_point, end_point)

        return [get_region(diagnostic) for diagnostic in self.diagnostics]

    def add_text_highligth(self, regions: List[sublime.Region]):
        self.view.add_regions(
            key="gotools_regions",
            regions=regions,
            scope="Comment",
            icon="dot",
            flags=sublime.DRAW_NO_OUTLINE
            | sublime.DRAW_NO_FILL
            | sublime.DRAW_SQUIGGLY_UNDERLINE,
        )

    def remove_text_highligth(self):
        self.view.erase_regions("gotools_regions")

    def set_diagnostics(self, diagnostics: dict) -> None:
        self.diagnostics = diagnostics

        self.remove_text_highligth()
        regions = self.get_get_highligth_regions()
        self.add_text_highligth(regions)

    def get_diagnostics(self):
        return self.diagnostics


class DiagnosticManager:
    def __init__(self, window: sublime.Window):
        self.window = window
        self.diagnostic_map = {}
        self.output_panel_name = "gotools_panel"

    def add(self, file_name: str, diagnostic: dict):
        self.diagnostic_map[file_name] = diagnostic

    def create_output_panel(self) -> None:
        """create output panel"""

        message_buffer = StringIO()

        def build_message(file_name: str, diagnostics: List[dict]):
            for diagnostic in diagnostics:
                short_name = os.path.basename(file_name)
                row = diagnostic["range"]["start"]["line"]
                col = diagnostic["range"]["start"]["character"]
                message = diagnostic["message"]
                source = diagnostic.get("source", "")

                # natural line index start with 1
                row += 1

                message_buffer.write(
                    f"{short_name}:{row}:{col}: {message} ({source})\n"
                )

        for file_name, diagnostics in self.diagnostic_map.items():
            build_message(file_name, diagnostics)

        panel = self.window.create_output_panel(self.output_panel_name)
        panel.set_read_only(False)
        panel.run_command(
            "append", {"characters": message_buffer.getvalue()},
        )

    def show_output_panel(self) -> None:
        """show output panel"""
        self.window.run_command(
            "show_panel", {"panel": f"output.{self.output_panel_name}"}
        )

    def destroy_output_panel(self, file_name: str):
        """destroy output panel"""
        self.window.destroy_output_panel(self.output_panel_name)

    def update_output_panel(self):
        self.create_output_panel()
        self.show_output_panel()


class FileWatchReport:
    def __init__(
        self,
        created: List[str] = None,
        changed: List[str] = None,
        deleted: List[str] = None,
    ):
        self.created = created or []
        self.changed = changed or []
        self.deleted = deleted or []

    def add_created(self, file_name):
        self.created.append(file_name)

    def add_changed(self, file_name):
        self.changed.append(file_name)

    def add_deleted(self, file_name):
        self.deleted.append(file_name)

    def __repr__(self):
        return (
            "FileWatchReport("
            f"created={self.created}, "
            f"changed={self.changed}, "
            f"deleted={self.deleted})"
        )


class FileWatcher:
    def __init__(self, root_path: str, pattern: str = "*"):
        self.root_path = root_path
        self.pattern = pattern

        self.cached_paths = {}

    def set_pattern(self, pattern: str):
        self.pattern = pattern

    def _watch(self):
        report = FileWatchReport()

        def add_created(file_name, modify_time):
            self.cached_paths[file_name] = modify_time
            report.add_created(file_name)

        def add_changed(file_name, modify_time):
            self.cached_paths[file_name] = modify_time
            report.add_changed(file_name)

        def add_deleted(file_name):
            del self.cached_paths[file_name]
            report.add_deleted(file_name)

        found_paths = set()
        for path in Path(self.root_path).glob(self.pattern):

            file_name = str(path)
            modify_time = path.stat().st_mtime
            found_paths.add(file_name)

            # file modified
            if file_name in self.cached_paths:
                if modify_time > self.cached_paths[file_name]:
                    add_changed(file_name, modify_time)

            # file created
            else:
                add_created(file_name, modify_time)

        # file removed
        for file_name in self.cached_paths.copy():
            if file_name not in found_paths:
                add_deleted(file_name)

        return report

    def watch(self) -> FileWatchReport:
        return self._watch()


GOPLS_CLIENT: lsp.LSPClient = None


class Workspace:
    """handle workspace"""

    def __init__(self, root_path: str, active_document: BufferedDocument = None):
        self.documents: Dict[str, BufferedDocument] = {}
        self.root_path = root_path
        self.active_document = active_document or BufferedDocument.from_active_view()
        self.is_initialized = False

        self.file_watcher = FileWatcher(root_path, "**/*.go")
        self.diagnostic_manager = DiagnosticManager(self.window())

    def initialize(self):
        GOPLS_CLIENT.initialize(self.root_path)

    def initialized(self):
        # watch files in project
        self.file_watcher.watch()

        GOPLS_CLIENT.initialized()
        self.is_initialized = True

    def set_active_document(self, file_name: str):
        try:
            self.active_document = self.documents[file_name]
        except KeyError:
            self.open_file(file_name)

    def get_document(self, file_name: str) -> BufferedDocument:
        if document := self.documents.get(file_name):
            return document
        raise DocumentNotFound("Document not found {file_name}")

    def open_file(self, file_name: str):
        if file_name in self.documents:
            LOGGER.debug("has opened")
            return

        document = BufferedDocument(file_name)
        self.documents[file_name] = document
        GOPLS_CLIENT.textDocument_didOpen(file_name, document.source(), 0)

        self.active_document = self.documents[file_name]

    def close_file(self, file_name: str):
        try:
            del self.documents[file_name]
            GOPLS_CLIENT.textDocument_didClose(file_name)
        except KeyError:
            # document not opened on TRANSIENT mode
            pass

    def save_file(self, file_name: str):
        GOPLS_CLIENT.textDocument_didSave(file_name)

    def window(self) -> sublime.Window:
        return sublime.active_window()

    def focus_document(self, document: BufferedDocument):
        self.window().focus_view(document.view)

    def watch_file_changes(self):
        def buld_item(path, type_):
            return {
                "uri": lsp.DocumentURI.from_path(path),
                "type": type_,
            }

        report = self.file_watcher.watch()
        created = [buld_item(path, 1) for path in report.created]
        changed = [buld_item(path, 2) for path in report.changed]
        deleted = [buld_item(path, 3) for path in report.deleted]
        changes = list(itertools.chain(created, changed, deleted))

        if changes:
            try:
                GOPLS_CLIENT.workspace_didChangeWatchedFiles(changes)
            except lsp.NotInitialized:
                pass

    def apply_diagnostics(self, params: dict):
        file_name = lsp.DocumentURI(params["uri"]).to_path()
        diagnostics = params["diagnostics"]

        if not file_name.startswith(self.root_path):
            LOGGER.debug(f"{file_name} not in workspace")
            return

        self.diagnostic_manager.add(file_name, diagnostics)
        self.diagnostic_manager.update_output_panel()

        try:
            document = self.get_document(file_name)
        except DocumentNotFound:
            pass
        else:
            document.set_diagnostics(diagnostics)

    def show_code_actions(self, actions: List[dict]):
        def build_title(action: dict):
            title = action["title"]
            kind = action["kind"]
            return f"{title} ({kind})"

        action_titles = [build_title(action) for action in actions]

        def select_action(index=-1):
            if index < 0:
                return

            if edit := actions[index].get("edit"):
                changes = edit["documentChanges"]
                self.apply_document_changes(changes)

            if command := actions[index].get("command"):
                GOPLS_CLIENT.workspace_executeCommand(command)

        self.window().show_quick_panel(
            action_titles, on_select=select_action, placeholder="select action"
        )

    def apply_document_changes(self, document_changes: List[dict]):

        active_document = self.active_document

        for change in document_changes:
            if TEXT_CHANGE_PROCESS.locked():
                LOGGER.debug("waiting change process")
                TEXT_CHANGE_SYNC.wait()
                LOGGER.debug("change process done")

            TEXT_CHANGE_SYNC.clear()
            file_name = lsp.DocumentURI(change["textDocument"]["uri"]).to_path()

            with TEXT_CHANGE_PROCESS:
                try:
                    document = self.get_document(file_name)
                    document.apply_text_changes(change["edits"])
                    document.save()

                except DocumentNotFound:
                    # modify file without buffer
                    document = UnbufferedDocument(file_name)
                    document.apply_text_changes(change["edits"])

        # focus active view
        self.focus_document(active_document)

    def prepare_rename(self, params: dict) -> None:
        # cursor at start rename
        placeholder = params["placeholder"]
        start = params["range"]["start"]
        row, col = start["line"], start["character"]
        file_name = self.active_document.file_name()
        self.input_rename(file_name, row, col, placeholder)

    def input_rename(self, file_name: str, row: int, col: int, placeholder: str):
        def rename_callback(new_name):
            GOPLS_CLIENT.textDocument_rename(file_name, row, col, new_name)

        self.window().show_input_panel(
            caption="rename :",
            initial_text=placeholder,
            on_done=rename_callback,
            on_change=None,
            on_cancel=None,
        )

    def show_definition(self, definitions: List[dict]):
        def build_location(definition: dict):
            file_name = lsp.DocumentURI(definition["uri"]).to_path()
            start = definition["range"]["start"]
            row = start["line"] + 1
            col = start["character"] + 1
            return f"{file_name}:{row}:{col}"

        locations = [build_location(definition) for definition in definitions]

        current_view = self.active_document.view
        selected_view = None

        def select_location(index=-1):
            nonlocal selected_view
            if index >= 0:
                # selected index start from zero
                selected_view = self.window().open_file(
                    locations[index],
                    flags=sublime.ENCODED_POSITION | sublime.TRANSIENT,
                )
            else:
                # cancel index = -1
                if selected_view.id() != current_view.id():
                    selected_view.close()

        self.window().show_quick_panel(
            locations,
            on_select=select_location,
            on_highlight=select_location,
            placeholder="select location",
        )


WORKSPACE: Workspace = None


class GoplsHandler(lsp.BaseHandler):
    def handle_initialize(self, params: dict) -> None:
        # TODO: implement
        WORKSPACE.initialized()
        WORKSPACE.open_file(WORKSPACE.active_document.file_name())

    def handle_window_logmessage(self, params: dict) -> None:
        print(params["message"])

    def handle_window_showmessage(self, params: dict) -> None:
        pass

    def handle_window_workdoneprogress_create(self, params: dict) -> str:
        return ""

    def handle_workspace_configuration(self, params: dict) -> List[dict]:
        return [{}]

    def handle_client_registercapability(self, params: dict) -> str:
        return ""

    def handle_s_progress(self, params: dict) -> None:
        value = params.get("value")
        if not value:
            return

        kind = value["kind"]
        message = value["message"]
        title = value.get("title")

        if kind == "begin":
            message = f"{title}: {message}"
            STATUS_MESSAGE.set_status(message)

        elif kind == "end":
            STATUS_MESSAGE.erase_status()
            STATUS_MESSAGE.show_message(message)

    def handle_textdocument_publishdiagnostics(self, params: dict) -> None:
        WORKSPACE.apply_diagnostics(params)

    def handle_textdocument_hover(self, params: dict) -> None:
        if result := params.get("result"):
            WORKSPACE.active_document.show_documentation(result)
        else:
            LOGGER.debug(params.get("error"))

    def handle_textdocument_completion(self, params: dict) -> None:
        if result := params.get("result"):
            WORKSPACE.active_document.show_completion(result)
        else:
            LOGGER.debug(params.get("error"))

    def handle_textdocument_formatting(self, params: dict) -> None:
        if result := params.get("result"):
            WORKSPACE.active_document.apply_text_changes(result)
        else:
            LOGGER.debug(params.get("error"))

    def handle_textdocument_codeaction(self, params: dict) -> None:
        if result := params.get("result"):
            WORKSPACE.show_code_actions(result)
        else:
            LOGGER.debug(params.get("error"))

    def handle_workspace_applyedit(self, params: dict) -> dict:
        is_applied = False
        try:
            document_changes = params["edit"]["documentChanges"]
            WORKSPACE.apply_document_changes(document_changes)
            is_applied = True
        except Exception as err:
            LOGGER.error(err, exc_info=True)

        LOGGER.debug("finish apply edit")
        return {"applied": is_applied}

    def handle_workspace_executecommand(self, params: dict) -> None:
        LOGGER.debug(f"workspace_executeCommand {params}")
        if error := params.get("error"):
            LOGGER.debug(error)

    def handle_textdocument_preparerename(self, params: dict) -> None:
        if result := params.get("result"):
            WORKSPACE.prepare_rename(result)
        else:
            LOGGER.debug(params.get("error"))

    def handle_textdocument_rename(self, params: dict) -> None:
        if result := params.get("result"):
            document_changes = result["documentChanges"]
            WORKSPACE.apply_document_changes(document_changes)
        else:
            LOGGER.debug(params.get("error"))

    def handle_textdocument_definition(self, params: dict) -> None:
        if result := params.get("result"):
            WORKSPACE.show_definition(result)
        else:
            LOGGER.debug(params.get("error"))


class SessionManager:
    def __init__(self):
        self.is_running = False
        self.lock = threading.Lock()

    def is_ready(self) -> bool:
        with self.lock:
            return self.is_running and WORKSPACE.is_initialized

    def start(self, root_path: str, working_file_name: str):
        if self.lock.locked():
            return

        global WORKSPACE
        with self.lock:
            GOPLS_CLIENT.run_server()
            self.is_running = True
            WORKSPACE = Workspace(root_path)
            WORKSPACE.initialize()

    def exit(self):
        global WORKSPACE
        with self.lock:
            GOPLS_CLIENT.exit()
            self.is_running = False
            WORKSPACE = None


SESSION_MANAGER: SessionManager = None


def main():
    global STATUS_MESSAGE
    global GOPLS_CLIENT
    global SESSION_MANAGER

    STATUS_MESSAGE = StatusMessage()
    transport = lsp.StandardIO("gopls", ["-vv"])
    handler = GoplsHandler()
    GOPLS_CLIENT = lsp.LSPClient(transport, handler)
    SESSION_MANAGER = SessionManager()


def plugin_loaded():
    main()


def plugin_unloaded():
    if SESSION_MANAGER and SESSION_MANAGER.is_running:
        SESSION_MANAGER.exit()


def valid_source(view: sublime.View):
    """if view is valid"""
    return view.match_selector(0, "source.go")


def valid_context(view: sublime.View, point: int):
    """if point in valid context"""

    # string
    if view.match_selector(point, "string"):
        return False
    # comment
    if view.match_selector(point, "comment"):
        return False
    return True


def get_root_folder(path: str):
    window: sublime.Window = sublime.active_window()
    if folders := [folder for folder in window.folders() if path.startswith(folder)]:
        return max(folders)

    raise ValueError(f"unable get root folder from {repr(path)}")


class EventListener(sublime_plugin.EventListener):
    """event listener"""

    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> sublime.CompletionList:
        point = locations[0]

        if not (valid_source(view) and valid_context(view, point)):
            return None

        if SESSION_MANAGER.is_ready():
            if completion := WORKSPACE.active_document.get_cached_completion():
                return sublime.CompletionList(
                    completion,
                    flags=sublime.INHIBIT_WORD_COMPLETIONS
                    | sublime.INHIBIT_EXPLICIT_COMPLETIONS,
                )

        file_name = view.file_name()
        if not SESSION_MANAGER.is_running:
            SESSION_MANAGER.start(get_root_folder(file_name), file_name)
            return

        WORKSPACE.set_active_document(file_name)
        row, col = view.rowcol_utf16(point)
        GOPLS_CLIENT.textDocument_completion(file_name, row, col)
        WORKSPACE.active_document.hide_completion()

    def on_hover(self, view: sublime.View, point: int, hover_zone: int):
        if not (valid_source(view) and valid_context(view, point)):
            return

        if hover_zone != sublime.HOVER_TEXT:
            return

        line = view.line(point)
        if point == line.b:
            # end of line may cause invalid location
            return

        file_name = view.file_name()
        if not SESSION_MANAGER.is_running:
            SESSION_MANAGER.start(get_root_folder(file_name), file_name)
            return

        WORKSPACE.set_active_document(file_name)
        row, col = view.rowcol_utf16(point)
        GOPLS_CLIENT.textDocument_hover(file_name, row, col)

    def on_activated(self, view: sublime.View):
        if not valid_source(view):
            return

        if not SESSION_MANAGER.is_ready():
            return

        WORKSPACE.watch_file_changes()
        WORKSPACE.set_active_document(view.file_name())

    def on_load(self, view: sublime.View):
        pass

    def on_reload(self, view: sublime.View):
        pass

    def on_pre_save(self, view: sublime.View):
        pass

    def on_post_save(self, view: sublime.View):
        if not valid_source(view):
            return

        if not SESSION_MANAGER.is_ready():
            return

        WORKSPACE.save_file(view.file_name())
        WORKSPACE.watch_file_changes()

    def on_pre_close(self, view: sublime.View):
        if not valid_source(view):
            return

        if not SESSION_MANAGER.is_ready():
            return

        WORKSPACE.close_file(view.file_name())

    def on_window_command(self, window: sublime.Window, name: str, args: dict):
        view = window.active_view()
        if not valid_source(view):
            return None

        if not SESSION_MANAGER.is_ready():
            return None

        if name == "goto_definition":
            if args is None:
                point = view.sel()[0].a
            else:
                xy = (args["event"]["x"], args["event"]["y"])
                point = view.window_to_text(xy)

            view.run_command("gotools_goto_definition", {"point": point})


class TextChangeListener(sublime_plugin.TextChangeListener):
    """listen text change"""

    def on_text_changed(self, changes: List[sublime.TextChange]):

        buffer: sublime.Buffer = self.buffer
        file_name = buffer.file_name()
        view = buffer.primary_view()

        if not valid_source(view):
            return

        if not SESSION_MANAGER.is_ready():
            return

        change_items = [self.build_items(view, change) for change in changes]

        WORKSPACE.active_document.increment_version()
        version = WORKSPACE.active_document.version

        GOPLS_CLIENT.textDocument_didChange(file_name, change_items, version)

    def build_items(self, view: sublime.View, change: sublime.TextChange):
        start: sublime.HistoricPosition = change.a
        end: sublime.HistoricPosition = change.b

        return {
            "range": {
                "end": {"character": end.col_utf16, "line": end.row},
                "start": {"character": start.col_utf16, "line": start.row},
            },
            "rangeLength": change.len_utf16,
            "text": change.str,
        }


class GotoolsDocumentFormattingCommand(sublime_plugin.TextCommand):
    """document formatting command"""

    def run(self, edit: sublime.Edit):
        if not valid_source(self.view):
            return

        if not SESSION_MANAGER.is_ready():
            return

        GOPLS_CLIENT.textDocument_formatting(self.view.file_name())

    def is_visible(self):
        return SESSION_MANAGER.is_ready() and valid_source(self.view)


class GotoolsCodeActionCommand(sublime_plugin.TextCommand):
    """code action command"""

    def run(self, edit: sublime.Edit):
        if not valid_source(self.view):
            return

        if not SESSION_MANAGER.is_ready():
            return

        selection = self.view.sel()[0]
        start_line, start_col = self.view.rowcol_utf16(selection.a)
        end_line, end_col = self.view.rowcol_utf16(selection.b)

        document: BufferedDocument = WORKSPACE.active_document

        GOPLS_CLIENT.textDocument_codeAction(
            file_name=document.file_name(),
            start_line=start_line,
            start_col=start_col,
            end_line=end_line,
            end_col=end_col,
            diagnostics=document.get_diagnostics(),
        )

    def is_visible(self):
        return SESSION_MANAGER.is_ready() and valid_source(self.view)


class GotoolsRenameCommand(sublime_plugin.TextCommand):
    """code action command"""

    def run(self, edit: sublime.Edit):
        if not valid_source(self.view):
            return

        if not SESSION_MANAGER.is_ready():
            return

        file_name = self.view.file_name()
        cursor = self.view.sel()[0].a
        row, col = self.view.rowcol_utf16(cursor)
        GOPLS_CLIENT.textDocument_prepareRename(file_name, row, col)

    def is_visible(self):
        return SESSION_MANAGER.is_ready() and valid_source(self.view)


class GotoolsGotoDefinitionCommand(sublime_plugin.TextCommand):
    """code action command"""

    def run(self, edit: sublime.Edit, point: Optional[int] = None):
        if not valid_source(self.view):
            return

        if not SESSION_MANAGER.is_ready():
            return

        file_name = self.view.file_name()
        if point is None:
            point = self.view.sel()[0].a

        row, col = self.view.rowcol_utf16(point)
        GOPLS_CLIENT.textDocument_definition(file_name, row, col)

    def is_visible(self):
        return SESSION_MANAGER.is_ready() and valid_source(self.view)


class GotoolsRestartServerCommand(sublime_plugin.TextCommand):
    """restart server"""

    def run(self, edit):
        LOGGER.info("GotoolsRestartServerCommand")
        SESSION_MANAGER.exit()

    def is_visible(self):
        return SESSION_MANAGER.is_running


class GotoolsInstallToolsCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        LOGGER.info("GotoolsInstallToolsCommand")
        thread = threading.Thread(target=tools.install_tools)
        thread.start()
