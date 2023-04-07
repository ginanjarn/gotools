"""C++ tools for Sublime Text"""

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from functools import wraps
from io import StringIO
from pathlib import Path
from typing import List, Dict


import sublime
import sublime_plugin
from sublime import HoverZone

from . import api

LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(levelname)s %(filename)s:%(lineno)d  %(message)s")
sh = logging.StreamHandler()
sh.setFormatter(fmt)
LOGGER.addHandler(sh)

# custom kind
KIND_PATH = (sublime.KIND_ID_VARIABLE, "p", "")
KIND_VALUE = (sublime.KIND_ID_VARIABLE, "u", "")
KIND_TEXT = (sublime.KIND_ID_VARIABLE, "t", "")
COMPLETION_KIND_MAP = defaultdict(
    lambda _: sublime.KIND_AMBIGUOUS,
    {
        1: KIND_TEXT,  # text
        2: sublime.KIND_FUNCTION,  # method
        3: sublime.KIND_FUNCTION,  # function
        4: sublime.KIND_FUNCTION,  # constructor
        5: sublime.KIND_VARIABLE,  # field
        6: sublime.KIND_VARIABLE,  # variable
        7: sublime.KIND_TYPE,  # class
        8: sublime.KIND_TYPE,  # interface
        9: sublime.KIND_NAMESPACE,  # module
        10: sublime.KIND_VARIABLE,  # property
        11: KIND_VALUE,  # unit
        12: KIND_VALUE,  # value
        13: sublime.KIND_NAMESPACE,  # enum
        14: sublime.KIND_KEYWORD,  # keyword
        15: sublime.KIND_SNIPPET,  # snippet
        16: KIND_VALUE,  # color
        17: KIND_PATH,  # file
        18: sublime.KIND_NAVIGATION,  # reference
        19: KIND_PATH,  # folder
        20: sublime.KIND_VARIABLE,  # enum member
        21: sublime.KIND_VARIABLE,  # constant
        22: sublime.KIND_TYPE,  # struct
        23: sublime.KIND_MARKUP,  # event
        24: sublime.KIND_MARKUP,  # operator
        25: sublime.KIND_TYPE,  # type parameter
    },
)


@dataclass
class TextChange:
    region: sublime.Region
    new_text: str
    cursor_move: int = 0

    def moved_region(self, move: int) -> sublime.Region:
        return sublime.Region(self.region.a + move, self.region.b + move)


DOCUMENT_CHAGE_EVENT = threading.Event()


class CpptoolsApplyTextChangesCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit, changes: List[dict]):
        text_changes = [self.to_text_change(c) for c in changes]
        try:
            self.apply(edit, text_changes)
        finally:
            DOCUMENT_CHAGE_EVENT.set()

    def to_text_change(self, change: dict) -> TextChange:
        start = change["range"]["start"]
        end = change["range"]["end"]

        start_point = self.view.text_point(start["line"], start["character"])
        end_point = self.view.text_point(end["line"], end["character"])

        region = sublime.Region(start_point, end_point)
        new_text = change["newText"]
        cursor_move = len(new_text) - region.size()

        return TextChange(region, new_text, cursor_move)

    def apply(self, edit: sublime.Edit, text_changes: List[TextChange]):
        cursor_move = 0
        for change in text_changes:
            replaced_region = change.moved_region(cursor_move)
            self.view.erase(edit, replaced_region)
            self.view.insert(edit, replaced_region.a, change.new_text)
            cursor_move += change.cursor_move


class UnbufferedDocument:
    def __init__(self, file_name: str):
        self._path = Path(file_name)
        self.text = self._path.read_text()

    def apply_text_changes(self, changes: List[dict]):
        try:
            self._apply_text_changes(changes)
        finally:
            DOCUMENT_CHAGE_EVENT.set()

    def _apply_text_changes(self, changes: List[dict]):
        lines = self.text.split("\n")

        for change in changes:
            # LOGGER.debug(f"apply change: {change}")
            try:
                start = change["range"]["start"]
                end = change["range"]["end"]
                new_text = change["newText"]

                start_line, start_character = start["line"], start["character"]
                end_line, end_character = end["line"], end["character"]

            except KeyError as err:
                raise Exception(f"invalid params {err}")

            new_lines = []
            # pre change line
            new_lines.extend(lines[:start_line])
            # changed lines
            prefix = lines[start_line][:start_character]
            suffix = lines[end_line][end_character:]
            changed_lines = f"{prefix}{new_text}{suffix}"
            new_lines.extend(changed_lines.split("\n"))
            # post change line
            new_lines.extend(lines[end_line + 1 :])
            # update
            lines = new_lines

        self.text = "\n".join(lines)

    def save(self):
        self._path.write_text(self.text)


class BufferedDocument:
    def __init__(self, view: sublime.View):
        self.view = view
        self.version = 0
        self.text = self._get_text()

        self.file_name = self.view.file_name()
        self._cached_completion = None

    def _get_text(self):
        if self.view.is_loading():
            # read from file
            return Path(self.file_name).read_text()

        return self.view.substr(sublime.Region(0, self.view.size()))

    def new_version(self) -> int:
        self.version += 1
        return self.version

    def document_uri(self) -> api.URI:
        return api.path_to_uri(self.file_name)

    @property
    def window(self) -> sublime.Window:
        return self.view.window()

    def save(self):
        self.view.run_command("save")

    def show_popup(self, text: str, row: int, col: int):
        point = self.view.text_point(row, col)
        self.view.run_command("markdown_popup", {"text": text, "point": point})

    def show_completion(self, items: List[dict]):
        def convert_kind(kind_num: int):
            return COMPLETION_KIND_MAP[kind_num]

        def build_completion(completion: dict):
            # sublime text has complete the header bracket '<> or ""'
            # remove it from clangd result
            text = completion["insertText"].rstrip('>"')
            annotation = completion["label"].rstrip('>"')
            kind = convert_kind(completion["kind"])

            return sublime.CompletionItem(
                trigger=text, completion=text, annotation=annotation, kind=kind
            )

        self._cached_completion = [build_completion(c) for c in items]
        self._trigger_completion()

    @property
    def cached_completion(self):
        temp = self._cached_completion
        self._cached_completion = None
        return temp

    def completion_ready(self) -> bool:
        return self._cached_completion is not None

    def _trigger_completion(self):
        LOGGER.debug("trigger completion")
        self.view.run_command(
            "auto_complete",
            {
                "disable_auto_insert": True,
                "next_completion_if_showing": True,
                "auto_complete_commit_on_tab": True,
            },
        )

    def hide_completion(self):
        self.view.run_command("hide_auto_complete")

    def apply_text_changes(self, changes: List[dict]):
        self.view.run_command("cpptools_apply_text_changes", {"changes": changes})

    def highlight_text(self, diagnostics: List[dict]):
        def get_region(diagnostic):
            start = diagnostic["range"]["start"]
            end = diagnostic["range"]["end"]

            start_point = self.view.text_point(start["line"], start["character"])
            end_point = self.view.text_point(end["line"], end["character"])
            return sublime.Region(start_point, end_point)

        regions = [get_region(d) for d in diagnostics]
        key = "cpptools_diagnostic"

        self.view.add_regions(
            key=key,
            regions=regions,
            scope="Comment",
            icon="dot",
        )


class DiagnosticPanel:
    OUTPUT_PANEL_NAME = "cpptools_panel"

    def __init__(self, window: sublime.Window, diagnostics_map: Dict[str, List[dict]]):
        self.window = window
        self.diagnostics_map = diagnostics_map

    def create_output_panel(self) -> None:
        """create output panel"""

        message_buffer = StringIO()

        def build_message(file_name: str, diagnostics: Dict[str, List[dict]]):
            for diagnostic in diagnostics:
                short_name = Path(file_name).name
                row = diagnostic["range"]["start"]["line"]
                col = diagnostic["range"]["start"]["character"]
                message = diagnostic["message"]
                source = diagnostic.get("source", "")

                # natural line index start with 1
                row += 1

                message_buffer.write(
                    f"{short_name}:{row}:{col}: {message} ({source})\n"
                )

        for file_name, diagnostics in self.diagnostics_map.items():
            build_message(file_name, diagnostics)

        panel = self.window.create_output_panel(self.OUTPUT_PANEL_NAME)
        panel.set_read_only(False)
        panel.run_command(
            "append",
            {"characters": message_buffer.getvalue()},
        )

    def show(self) -> None:
        """show output panel"""
        self.create_output_panel()
        self.window.run_command(
            "show_panel", {"panel": f"output.{self.OUTPUT_PANEL_NAME}"}
        )

    def destroy(self):
        """destroy output panel"""
        self.window.destroy_output_panel(self.OUTPUT_PANEL_NAME)


class Client(api.BaseHandler):
    def __init__(self):
        self.transport = api.Transport(self)
        self.active_document: BufferedDocument = None
        self.working_documents: dict[str, BufferedDocument] = {}

        self._initialized = False
        self.diagnostics_map = {}

        self.diagnostics_panel = DiagnosticPanel(
            self.active_window(), self.diagnostics_map
        )

    initialized_event = threading.Event()

    def wait_initialized(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            Client.initialized_event.wait()
            return func(*args, **kwargs)

        return wrapper

    def ready(self) -> bool:
        return self.transport.is_running() and self._initialized

    run_server_lock = threading.Lock()

    def run_server(self):
        # only one thread can run server
        with self.run_server_lock:
            if not self.transport.is_running():
                self.transport.run_server()

    def exit(self):
        """exit session"""
        self._initialized = False
        self.initialized_event.clear()
        self.transport.terminate_server()

    def active_window(self) -> sublime.Window:
        return sublime.active_window()

    def initialize(self, workspace_path: str):
        self.transport.send_request(
            "initialize",
            {
                "rootPath": workspace_path,
                "rootUri": api.path_to_uri(workspace_path),
                "capabilities": {
                    "textDocument": {
                        "hover": {
                            "contentFormat": ["markdown", "plaintext"],
                        }
                    }
                },
            },
        )

    def handle_initialize(self, params: dict):
        if err := params.get("error"):
            print(err["message"])
            return

        self.transport.send_notification("initialized", {})
        self._initialized = True
        self.initialized_event.set()

    @wait_initialized
    def textdocument_didopen(self, file_name: str, *, reload: bool = False):
        if (not reload) and file_name in self.working_documents:
            self.active_document = self.working_documents[file_name]
            return

        view = self.active_window().find_open_file(file_name)
        self.working_documents[file_name] = BufferedDocument(view)
        self.active_document = self.working_documents[file_name]

        self.transport.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "languageId": "cpp",
                    "text": self.active_document.text,
                    "uri": self.active_document.document_uri(),
                    "version": self.active_document.version,
                }
            },
        )

    def textdocument_didsave(self, file_name: str):
        if document := self.working_documents.get(file_name):
            self.transport.send_notification(
                "textDocument/didSave",
                {"textDocument": {"uri": document.document_uri()}},
            )

        else:
            # untitled document not yet loaded to clangd
            self.textdocument_didopen(file_name)

    def textdocument_didclose(self, file_name: str):
        if document := self.working_documents.get(file_name):
            self.transport.send_notification(
                "textDocument/didClose",
                {"textDocument": {"uri": document.document_uri()}},
            )
            del self.working_documents[file_name]

    @wait_initialized
    def textdocument_didchange(self, file_name: str, changes: List[dict]):
        if document := self.working_documents.get(file_name):
            self.transport.send_notification(
                "textDocument/didChange",
                {
                    "contentChanges": changes,
                    "textDocument": {
                        "uri": document.document_uri(),
                        "version": document.new_version(),
                    },
                },
            )

    @wait_initialized
    def textdocument_hover(self, file_name, row, col):
        if document := self.working_documents.get(file_name):
            self.transport.send_request(
                "textDocument/hover",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": document.document_uri()},
                },
            )

    def handle_textdocument_hover(self, params: dict):
        if err := params.get("error"):
            print(err["message"])

        elif result := params.get("result"):
            try:
                message = result["contents"]["value"]
                start = result["range"]["start"]
                row, col = start["line"], start["character"]
            except Exception:
                pass
            else:
                self.active_document.show_popup(message, row, col)

    @wait_initialized
    def textdocument_completion(self, file_name, row, col):
        if document := self.working_documents.get(file_name):
            self.transport.send_request(
                "textDocument/completion",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": document.document_uri()},
                },
            )

    def handle_textdocument_completion(self, params: dict):
        if err := params.get("error"):
            print(err["message"])

        elif result := params.get("result"):
            try:
                items = result["items"]
            except Exception:
                pass
            else:
                self.active_document.show_completion(items)

    def handle_textdocument_publishdiagnostics(self, params: dict):
        file_name = api.uri_to_path(params["uri"])
        diagnostics = params["diagnostics"]

        self.diagnostics_map[file_name] = diagnostics
        self.diagnostics_panel.show()

        if document := self.working_documents.get(file_name):
            document.highlight_text(diagnostics)

    @wait_initialized
    def textdocument_formatting(self, file_name):
        if document := self.working_documents.get(file_name):
            self.transport.send_request(
                "textDocument/formatting",
                {
                    "options": {"insertSpaces": True, "tabSize": 2},
                    "textDocument": {"uri": document.document_uri()},
                },
            )

    def handle_textdocument_formatting(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self.active_document.apply_text_changes(result)

    @wait_initialized
    def textdocument_codeaction(
        self, file_name, start_row, start_col, end_row, end_col
    ):
        if document := self.working_documents.get(file_name):

            self.transport.send_request(
                "textDocument/codeAction",
                {
                    "context": {
                        "diagnostics": self.diagnostics_map.get(file_name, []),
                        "triggerKind": 2,
                    },
                    "range": {
                        "end": {"character": end_col, "line": end_row},
                        "start": {"character": start_col, "line": start_row},
                    },
                    "textDocument": {"uri": document.document_uri()},
                },
            )

    def handle_textdocument_codeaction(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._show_codeaction(result)

    def _show_codeaction(self, actions: List[dict]):
        def on_select(index):
            if index < 0:
                return

            action = actions[index]
            if edit := action.get("edit"):
                self._apply_edit(edit)
            elif action.get("command"):
                self.transport.send_request("workspace/executeCommand", action)

        def get_title(action: dict) -> str:
            title = action["title"]
            if kind := action.get("kind"):
                return f"({kind}){title}"
            return title

        self.active_window().show_quick_panel(
            items=[get_title(i) for i in actions],
            on_select=on_select,
            flags=sublime.MONOSPACE_FONT,
            placeholder="Code actions...",
        )

    def _apply_edit(self, edit: dict):
        try:
            for file_uri, changes in edit["changes"].items():
                DOCUMENT_CHAGE_EVENT.clear()
                file_name = api.uri_to_path(file_uri)
                document = self.working_documents.get(
                    file_name, UnbufferedDocument(file_name)
                )
                document.apply_text_changes(changes)
                # wait until changes applied
                DOCUMENT_CHAGE_EVENT.wait()
                document.save()

        except Exception as err:
            LOGGER.exception(err)
            raise err

    def handle_workspace_applyedit(self, params: dict) -> dict:
        try:
            self._apply_edit(params["edit"])
        except Exception as err:
            LOGGER.error(err, exc_info=True)
            return {"applied": False}
        else:
            return {"applied": True}

    def handle_workspace_executecommand(self, params: dict) -> dict:
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            LOGGER.info(result)

    @wait_initialized
    def textdocument_declaration(self, file_name, row, col):
        if document := self.working_documents.get(file_name):
            self.transport.send_request(
                "textDocument/declaration",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": document.document_uri()},
                },
            )

    @wait_initialized
    def textdocument_definition(self, file_name, row, col):
        if document := self.working_documents.get(file_name):

            self.transport.send_request(
                "textDocument/definition",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": document.document_uri()},
                },
            )

    def _open_locations(self, locations: List[dict]):
        current_view = self.active_document.view
        current_sel = tuple(current_view.sel())

        def build_location(location: dict):
            file_name = api.uri_to_path(location["uri"])
            row = location["range"]["start"]["line"]
            col = location["range"]["start"]["character"]
            return f"{file_name}:{row+1}:{col+1}"

        locations = [build_location(l) for l in locations]

        def open_location(index):
            if index < 0:
                self.active_window().focus_view(current_view)
                current_view.sel().clear()
                current_view.sel().add_all(current_sel)

            else:
                flags = sublime.ENCODED_POSITION
                self.active_window().open_file(locations[index], flags=flags)

        def preview_location(index):
            flags = sublime.ENCODED_POSITION | sublime.TRANSIENT
            self.active_window().open_file(locations[index], flags=flags)

        self.active_window().show_quick_panel(
            items=locations,
            on_select=open_location,
            flags=sublime.MONOSPACE_FONT,
            on_highlight=preview_location,
            placeholder="Open location...",
        )

    def handle_textdocument_declaration(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._open_locations(result)

    def handle_textdocument_definition(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._open_locations(result)

    @wait_initialized
    def textdocument_preparerename(self, row, col):
        self.transport.send_request(
            "textDocument/prepareRename",
            {
                "position": {"character": col, "line": row},
                "textDocument": {"uri": self.active_document.document_uri()},
            },
        )

    @wait_initialized
    def textdocument_rename(self, new_name, row, col):
        self.transport.send_request(
            "textDocument/rename",
            {
                "newName": new_name,
                "position": {"character": col, "line": row},
                "textDocument": {"uri": self.active_document.document_uri()},
            },
        )

    def _input_rename(self, symbol_location: dict):
        start = symbol_location["start"]
        start_point = self.active_document.view.text_point(
            start["line"], start["character"]
        )
        end = symbol_location["end"]
        end_point = self.active_document.view.text_point(end["line"], end["character"])

        def request_rename(new_name):
            self.textdocument_rename(new_name, start["line"], start["character"])

        self.active_window().show_input_panel(
            caption="rename",
            initial_text=self.active_document.view.substr(
                sublime.Region(start_point, end_point)
            ),
            on_done=request_rename,
            on_change=None,
            on_cancel=None,
        )

    def handle_textdocument_preparerename(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._input_rename(result)

    def handle_textdocument_rename(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._apply_edit(result)


CLIENT: Client = None


def main():
    global CLIENT
    CLIENT = Client()


def plugin_loaded():
    main()


def plugin_unloaded():
    if CLIENT:
        CLIENT.exit()


def valid_context(view: sublime.View, point: int):
    return view.match_selector(point, "source.c++")


def get_workspace_path(view: sublime.View) -> str:
    window = view.window()
    file_name = view.file_name()

    if folders := [
        folder for folder in window.folders() if file_name.startswith(folder)
    ]:
        return max(folders)
    return str(Path(file_name).parent)


class ViewEventListener(sublime_plugin.ViewEventListener):
    def on_hover(self, point: int, hover_zone: HoverZone):
        # check point in valid source
        if not (valid_context(self.view, point) and hover_zone == sublime.HOVER_TEXT):
            return

        file_name = self.view.file_name()
        row, col = self.view.rowcol(point)

        threading.Thread(
            target=self._on_hover, args=(self.view, file_name, row, col)
        ).start()

    def _on_hover(self, view, file_name, row, col):
        # check if server available
        try:
            if CLIENT.ready():
                # request on hover
                CLIENT.textdocument_hover(file_name, row, col)
            else:
                # initialize server
                CLIENT.run_server()
                CLIENT.initialize(get_workspace_path(view))
                CLIENT.textdocument_didopen(file_name)
                CLIENT.textdocument_hover(file_name, row, col)

        except api.ServerNotRunning:
            pass

    prev_completion_loc = 0

    def on_query_completions(
        self, prefix: str, locations: List[int]
    ) -> sublime.CompletionList:

        point = locations[0]

        # check point in valid source
        if not valid_context(self.view, point):
            return

        if (document := CLIENT.active_document) and document.completion_ready():

            show = False
            word = self.view.word(self.prev_completion_loc)
            if point == self.prev_completion_loc:
                show = True
            elif self.view.substr(word).isidentifier() and point in word:
                show = True

            if show:
                LOGGER.debug("show auto_complete")
                return sublime.CompletionList(
                    document.cached_completion, flags=sublime.INHIBIT_WORD_COMPLETIONS
                )

            LOGGER.debug("hide auto_complete")
            document.hide_completion()
            return

        self.prev_completion_loc = point
        file_name = self.view.file_name()
        row, col = self.view.rowcol(point)

        threading.Thread(
            target=self._on_query_completions, args=(self.view, file_name, row, col)
        ).start()

        self.view.run_command("hide_auto_complete")

    def _on_query_completions(self, view, file_name, row, col):
        # check if server available
        try:
            if CLIENT.ready():
                # request on hover
                CLIENT.textdocument_completion(file_name, row, col)
            else:
                # initialize server
                CLIENT.run_server()
                CLIENT.initialize(get_workspace_path(self.view))
                CLIENT.textdocument_didopen(file_name)
                CLIENT.textdocument_completion(file_name, row, col)

        except api.ServerNotRunning:
            pass

    def on_activated(self):
        # check point in valid source
        if not valid_context(self.view, 0):
            return

        if CLIENT.ready():
            CLIENT.textdocument_didopen(self.view.file_name())

    def on_post_save(self):
        # check point in valid source
        if not valid_context(self.view, 0):
            return

        if CLIENT.ready():
            CLIENT.textdocument_didsave(self.view.file_name())

    def on_close(self):
        # check point in valid source
        if not valid_context(self.view, 0):
            return

        if CLIENT.ready():
            CLIENT.textdocument_didclose(self.view.file_name())

    def on_load(self):
        # check point in valid source
        if not valid_context(self.view, 0):
            return

        if CLIENT.ready():
            CLIENT.textdocument_didopen(self.view.file_name(), reload=True)

    def on_reload(self):
        # check point in valid source
        if not valid_context(self.view, 0):
            return

        if CLIENT.ready():
            CLIENT.textdocument_didopen(self.view.file_name(), reload=True)

    def on_revert(self):
        # check point in valid source
        if not valid_context(self.view, 0):
            return

        if CLIENT.ready():
            CLIENT.textdocument_didopen(self.view.file_name(), reload=True)


class TextChangeListener(sublime_plugin.TextChangeListener):
    def on_text_changed(self, changes: List[sublime.TextChange]):
        view = self.buffer.primary_view()
        if not valid_context(view, 0):
            return

        if not CLIENT.ready():
            return

        file_name = self.buffer.file_name()
        CLIENT.textdocument_didopen(file_name)
        CLIENT.textdocument_didchange(
            file_name, [self.change_as_rpc(c) for c in changes]
        )

    @staticmethod
    def change_as_rpc(change: sublime.TextChange) -> dict:
        start = change.a
        end = change.b
        return {
            "range": {
                "end": {"character": end.col, "line": end.row},
                "start": {"character": start.col, "line": start.row},
            },
            "rangeLength": change.len_utf8,
            "text": change.str,
        }


class CpptoolsDocumentFormattingCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit):
        file_name = self.view.file_name()
        if CLIENT.ready():
            CLIENT.textdocument_didopen(file_name)
            CLIENT.textdocument_formatting(file_name)

    def is_visible(self):
        return valid_context(self.view, 0)


class CpptoolsCodeActionCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit):
        file_name = self.view.file_name()
        cursor = self.view.sel()[0]
        if CLIENT.ready():
            CLIENT.textdocument_didopen(file_name)
            start_row, start_col = self.view.rowcol(cursor.a)
            end_row, end_col = self.view.rowcol(cursor.b)
            CLIENT.textdocument_codeaction(
                file_name, start_row, start_col, end_row, end_col
            )

    def is_visible(self):
        return valid_context(self.view, 0)


class CpptoolsGotoDefinitionCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit):
        file_name = self.view.file_name()
        cursor = self.view.sel()[0]
        if CLIENT.ready():
            CLIENT.textdocument_didopen(file_name)
            start_row, start_col = self.view.rowcol(cursor.a)
            CLIENT.textdocument_definition(file_name, start_row, start_col)

    def is_visible(self):
        return valid_context(self.view, 0)


class CpptoolsGotoDeclarationCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit):
        file_name = self.view.file_name()
        cursor = self.view.sel()[0]
        if CLIENT.ready():
            CLIENT.textdocument_didopen(file_name)
            start_row, start_col = self.view.rowcol(cursor.a)
            CLIENT.textdocument_declaration(file_name, start_row, start_col)

    def is_visible(self):
        return valid_context(self.view, 0)


class CpptoolsRenameCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit):
        file_name = self.view.file_name()
        cursor = self.view.sel()[0]
        if CLIENT.ready():
            CLIENT.textdocument_didopen(file_name)
            start_row, start_col = self.view.rowcol(cursor.a)
            CLIENT.textdocument_preparerename(start_row, start_col)

    def is_visible(self):
        return valid_context(self.view, 0)
