"""gotools main app"""

import logging
import os
import threading
import time

from typing import List, Union, Dict, Iterator, Any

import sublime
import sublime_plugin

from .api import lsp
from .api.lsp import StandardIO, ServerOffline, DocumentURI
from .third_party import mistune


LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)


# custom kind
KIND_PATH = (sublime.KIND_ID_NAVIGATION, "p", "")
KIND_VALUE = (sublime.KIND_ID_NAVIGATION, "u", "")

_KIND_MAP = {
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


class CompletionList(sublime.CompletionList):
    """CompletionList"""

    @staticmethod
    def build_completion(rpc_items: Dict[str, object]):
        """build completion item"""

        for item in rpc_items:

            trigger = item["filterText"]
            annotation = item.get("detail", "")
            kind = _KIND_MAP.get(item["kind"], sublime.KIND_AMBIGUOUS)

            text_changes = item["textEdit"]
            additional_text_edits = item.get("additionalTextEdits")
            if additional_text_edits is not None:
                yield sublime.CompletionItem.command_completion(
                    trigger=trigger,
                    command="gotools_apply_completion",
                    args={
                        "completion": text_changes,
                        "additional_changes": additional_text_edits,
                    },
                    annotation=annotation,
                    kind=kind,
                )
                continue

            # default
            yield sublime.CompletionItem(
                trigger=trigger,
                annotation=annotation,
                completion=text_changes["newText"],
                completion_format=sublime.COMPLETION_FORMAT_SNIPPET,
                kind=kind,
            )

    @classmethod
    def from_rpc(cls, completion_items: List[dict]):
        """load from rpc"""

        LOGGER.debug("completion_list: %s", completion_items)

        return cls(
            completions=list(cls.build_completion(completion_items))
            if completion_items
            else [],
            flags=sublime.INHIBIT_WORD_COMPLETIONS
            | sublime.INHIBIT_EXPLICIT_COMPLETIONS,
        )


class GotoolsApplyCompletionCommand(sublime_plugin.TextCommand):
    def run(self, edit, completion, additional_changes):
        # gopls insert completion at first
        completion["range"]["end"] = completion["range"]["start"]
        self.view.run_command(
            "gotools_apply_document_change", {"changes": [completion]}
        )
        self.view.run_command(
            "gotools_apply_document_change", {"changes": additional_changes}
        )


class DiagnosticItem:
    """diagnostic item"""

    def __init__(self, region: sublime.Region, severity: int, message: str):
        self.region = region
        self.severity = severity
        self.message = message

    @classmethod
    def from_rpc(cls, view: sublime.View, *, diagnostic: dict):
        """from rpc"""
        range_ = diagnostic["range"]
        start = view.text_point(range_["start"]["line"], range_["start"]["character"])
        end = view.text_point(range_["end"]["line"], range_["end"]["character"])
        return cls(
            sublime.Region(start, end), diagnostic["severity"], diagnostic["message"]
        )


class Diagnostics:
    """Diagnostic hold diagnostic data at view"""

    REGION_KEYS = {
        1: "gotools.error",
        2: "gotools.warning",
        3: "gotools.information",
        4: "gotools.hint",
    }

    def __init__(self, view: sublime.View):
        self.view = view
        self.window = self.view.window()
        self.message_map = {}
        self.outputpanel_name = f"gotools:{self.view.file_name()}"

    def add_regions(
        self, key: str, regions: List[sublime.Region], *, error_region: bool = False
    ):
        """add syntax highlight regions"""

        self.view.add_regions(
            key=key,
            regions=regions,
            scope="Comment",
            icon="circle" if error_region else "dot",
            flags=(
                sublime.DRAW_NO_FILL
                | sublime.DRAW_NO_OUTLINE
                | sublime.DRAW_SOLID_UNDERLINE
            ),
        )

    # Diagnostic severity
    ERROR = 1
    WARNING = 2
    INFO = 3
    HINT = 4

    def set_diagnostics(self, diagnostics: List[dict]):
        """set diagnostic

        * set message_map
        * apply syntax highlight
        """

        error_region = []
        warning_region = []
        information_region = []
        hint_region = []

        diagnostic_items = [
            DiagnosticItem.from_rpc(self.view, diagnostic=diagnostic)
            for diagnostic in diagnostics
        ]

        for diagnostic in diagnostic_items:
            row, col = self.view.rowcol(diagnostic.region.a)
            self.message_map[(row, col)] = diagnostic.message

            if diagnostic.severity == self.ERROR:
                error_region.append(diagnostic.region)
            elif diagnostic.severity == self.WARNING:
                warning_region.append(diagnostic.region)
            elif diagnostic.severity == self.INFO:
                information_region.append(diagnostic.region)
            elif diagnostic.severity == self.HINT:
                hint_region.append(diagnostic.region)

        # clear if any highlight in view
        self.erase_highlight()

        self.add_regions(
            key=self.REGION_KEYS[self.ERROR], regions=error_region, error_region=True
        )
        self.add_regions(key=self.REGION_KEYS[self.WARNING], regions=warning_region)
        self.add_regions(key=self.REGION_KEYS[self.INFO], regions=information_region)
        self.add_regions(key=self.REGION_KEYS[self.HINT], regions=hint_region)

    def erase_highlight(self):
        """erase highlight"""

        for _, value in self.REGION_KEYS.items():
            self.view.erase_regions(value)

    def show_panel(self) -> None:
        """show output panel"""

        def build_message(mapping: Dict[tuple, str]):
            short_name = os.path.basename(self.view.file_name())
            for key, val in mapping.items():
                row, col = key
                yield f"{short_name}:{row+1}:{col} {val}"

        if self.message_map:

            # create new panel
            panel = self.window.create_output_panel(self.outputpanel_name)
            message = "\n".join(build_message(self.message_map))

            panel.set_read_only(False)
            panel.run_command(
                "append", {"characters": message},
            )

        self.window.run_command(
            "show_panel", {"panel": f"output.{self.outputpanel_name}"}
        )

    def destroy_panel(self):
        """destroy output panel"""
        self.window.destroy_output_panel(self.outputpanel_name)


class ChangeItem:
    """this class hold change data"""

    def __init__(self, region: sublime.Region, old_text: str, new_text: str):
        self.region = region
        self.old_text = old_text
        self.new_text = new_text

        # cursor position move
        self.cursor_move = len(new_text) - region.size()

    def get_region(self, cursor_move: int = 0):
        """get region with adjusted position to cursor move"""
        return sublime.Region(self.region.a + cursor_move, self.region.b + cursor_move)

    def __repr__(self):
        return (
            f"ChangeItem({repr(self.region)}, "
            f"{repr(self.old_text)}, {repr(self.new_text)}, "
            f"{self.cursor_move})"
        )

    @classmethod
    def from_rpc(cls, view: sublime.View, *, change: Dict):
        """from rpc"""

        range_ = change["range"]
        new_text = change["newText"]

        start = view.text_point(range_["start"]["line"], range_["start"]["character"])
        end = view.text_point(range_["end"]["line"], range_["end"]["character"])

        region = sublime.Region(start, end)
        old_text = view.substr(region)
        return cls(region, old_text, new_text)


class DocumentChangeSync:
    """Document change sync prevent multiple file changes at same time"""

    _lock = threading.Lock()

    def __init__(self):
        self.busy = False

    def set_busy(self):
        with self._lock:
            self.busy = True

    def set_finished(self):
        with self._lock:
            self.busy = False


DOCUMENT_CHANGE_SYNC = DocumentChangeSync()


class GotoolsApplyDocumentChangeCommand(sublime_plugin.TextCommand):
    """apply document change to view"""

    def run(self, edit: sublime.Edit, changes: list):
        LOGGER.info("GotoolsApplyDocumentChangeCommand")

        LOGGER.debug("apply changes to %s", self.view.file_name())
        LOGGER.debug("changes: %s", changes)
        view: sublime.View = self.view
        LOGGER.debug(f"{view.file_name()} is loading: {view.is_loading()}")

        list_change_item: List[ChangeItem] = [
            ChangeItem.from_rpc(self.view, change=change) for change in changes
        ]
        try:
            self.apply(edit, list_change_item)
        except Exception as err:
            LOGGER.error(err, exc_info=True)

    def apply(self, edit, list_change_item):
        def sort_by_region(item: ChangeItem):
            return item.region

        # prevent change collision
        list_change_item.sort(key=sort_by_region)

        # this hold cursor movement
        cursor_move = 0

        for change in list_change_item:
            region = change.get_region(cursor_move)
            self.view.erase(edit, region)
            self.view.insert(edit, region.a, change.new_text)
            cursor_move += change.cursor_move

        DOCUMENT_CHANGE_SYNC.set_finished()


class ActiveDocument:
    """commands to active view"""

    def __init__(self):

        self._completion_result = None
        self._window: sublime.Window = None
        self._view: sublime.View = None

    @property
    def window(self):
        if self._window is None:
            self.window = sublime.active_window()
            return self._window
        return self._window

    @window.setter
    def window(self, value):
        self._window = value

    @property
    def view(self):
        if self._view is None:
            self._view = self.window.active_view()
        return self._view

    @view.setter
    def view(self, value):
        self._view = value

    def get_completion_result(self):
        result = self._completion_result
        self._completion_result = None
        return result

    def show_completions(self, completions):

        if completions is None:
            return

        completions = completions["items"]
        completion_list = CompletionList.from_rpc(completions)
        self._completion_result = completion_list

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

    @staticmethod
    def adapt_minihtml(lines: str) -> Iterator[str]:
        """adapt sublime minihtml tag

        Not all html tag implemented
        """
        pre_tag = False
        for line in lines.splitlines():

            if line.startswith("<pre>"):
                pre_tag = True
            elif pre_tag and line.endswith("</pre>"):
                pre_tag = False

            line = line.replace("<pre>", "<div class='code_block'>")
            line = line.replace("</pre>", "</div>")
            line = line.replace("  ", "&nbsp;&nbsp;")
            line = f"{line}<br />" if pre_tag else line

            yield line

    def show_popup(self, documentation):

        if documentation is None:
            return

        contents = documentation["contents"]["value"]
        kind = documentation["contents"]["kind"]
        start = documentation["range"]["start"]
        location = self.view.text_point(start["line"], start["character"])

        if kind == "markdown":
            contents = mistune.markdown(contents)

        style = """
        body {
            font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif,"Apple Color Emoji","Segoe UI Emoji";
            line-height: 1.5;
        }
        code {
            background-color: color(var(--background) alpha(0.8));
            font-family: ui-monospace,SFMono-Regular,SF Mono,Menlo,Consolas,Liberation Mono,monospace;
        }
        .code_block {
            background-color: color(var(--background) alpha(0.8));
        }
        """
        contents = "\n".join(self.adapt_minihtml(contents))
        contents = f"<style>{style}</style>\n{contents}"
        LOGGER.debug(contents)
        self.view.show_popup(
            contents,
            flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            location=location,
            max_width=1024,
        )

    def show_code_action(self, action_params: List[dict]):
        def on_done(index=-1):
            if index > -1:
                self.view.run_command(
                    "gotools_workspace_exec_command",
                    {"params": action_params[index]["command"]},
                )

        items = [item["title"] for item in action_params]
        self.window.show_quick_panel(items, on_done)

    def apply_document_change(self, changes: List[dict]):

        # wait until view loaded
        while True:
            LOGGER.debug("loading %s", self.view.file_name())
            if self.view.is_loading():
                time.sleep(0.5)
                continue
            break

        self.view.run_command("gotools_apply_document_change", {"changes": changes})

    def prepare_rename(self, params):
        start = params.result["start"]
        end = params.result["end"]
        placeholder = self.view.substr(
            sublime.Region(
                self.view.text_point(start["line"], start["character"]),
                self.view.text_point(end["line"], end["character"]),
            )
        )

        self.input_rename(start["line"], start["character"], placeholder)

    def input_rename(self, row, col, placeholder: str):
        def apply_rename(new_name):
            self.view.run_command(
                "gotools_rename", {"row": row, "col": col, "new_name": new_name}
            )

        self.window.show_input_panel(
            caption="rename",
            initial_text=placeholder,
            on_done=apply_rename,
            on_change=None,
            on_cancel=None,
        )

    def goto(self, params: List[dict]):
        LOGGER.debug("goto: %s", params)

        def get_location(location: Dict[str, object]):
            file_name = DocumentURI(location["uri"]).to_path()
            start = location["range"]["start"]
            row, col = start["line"] + 1, start["character"] + 1
            return f"{file_name}:{row}:{col}"

        locations = [get_location(item) for item in params]

        def on_select(index=-1):
            if index > -1:
                self.window.open_file(locations[index], flags=sublime.ENCODED_POSITION)

        self.window.show_quick_panel(
            items=locations, on_select=on_select, flags=sublime.MONOSPACE_FONT
        )


class Document:
    """Document handler"""

    def __init__(self, file_name: str, *, force_open: bool = False):

        if force_open:
            self.view: sublime.View = sublime.active_window().open_file(file_name)
        else:
            self.view: sublime.View = sublime.active_window().find_open_file(file_name)

        self.window: sublime.Window = self.view.window()

    def focus_view(self):
        self.window.focus_view(self.view)

    def apply_document_change(self, changes: List[dict]):

        # wait until view loaded
        while True:
            LOGGER.debug("loading %s", self.view.file_name())
            if self.view.is_loading():
                time.sleep(0.5)
                continue
            break

        self.view.run_command("gotools_apply_document_change", {"changes": changes})

    def apply_diagnostics(self, diagnostics_item: List[dict]):

        if DOCUMENT_CHANGE_SYNC.busy:
            LOGGER.debug("in document change process")
            return

        LOGGER.debug("apply diagnostics to: %s", self.view.file_name())
        diagnostics = Diagnostics(self.view)
        diagnostics.set_diagnostics(diagnostics_item)
        diagnostics.show_panel()

    def clear_diagnostics(self):
        diagnostic = Diagnostics(self.view)
        try:
            diagnostic.erase_highlight()
            diagnostic.destroy_panel()

        except Exception as err:
            LOGGER.error(err)

    def show_diagnostics(self):
        diagnostic = Diagnostics(self.view)
        diagnostic.show_panel()

    def set_status(self, message: str):
        self.view.set_status("gotools_status", message)

    def erase_status(self):
        self.view.erase_status("gotools_status")


ACTIVE_DOCUMENT: ActiveDocument = ActiveDocument()


class GoplsClient(lsp.LSPClient):
    """LSP client listener"""

    def __init__(self):
        super().__init__()
        self.transport: lsp.AbstractTransport = None

        self.completion_commit_character = []
        self.initialize_options = {}

    def run_server(self, gopls="gopls", *args):
        commands = [gopls, "-rpc.trace", "-vv"]
        commands.extend(args)
        try:
            self.transport = StandardIO(commands)
            self._register_commands()

        except Exception as err:
            LOGGER.error("running server error", exc_info=True)
        else:
            self.server_running = True

    def _hide_completion(self, character: str):
        LOGGER.info("_hide_completion")

        if character in self.completion_commit_character:
            ACTIVE_DOCUMENT.hide_completion()

    def shutdown_server(self):
        LOGGER.debug("shutdown_server")
        if self.server_running:
            self.reset_session()

    def handle_initialize(self, message: lsp.RPCMessage):
        LOGGER.info("handle_initialize")

        LOGGER.debug("params: %s", message)
        # FIXME: handle initialize
        # ------------------------
        if message.error:
            LOGGER.error(message.error)
            return

        capabilities = message.result["capabilities"]

        self.is_initialized = True

        # notify if initialized
        self.transport.notify(lsp.RPCMessage.notification("initialized", {}))

    def handle_textDocument_completion(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_completion")

        if message.error:
            LOGGER.error(message.error)
            return

        ACTIVE_DOCUMENT.show_completions(message.result)

    def handle_textDocument_hover(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_hover")

        if message.error:
            LOGGER.error(message.error)
            return

        ACTIVE_DOCUMENT.show_popup(message.result)

    def handle_textDocument_formatting(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_formatting")

        if message.error:
            LOGGER.error(message.error)
            return

        changes = message.result
        try:
            ACTIVE_DOCUMENT.apply_document_change(changes)
        except Exception as err:
            LOGGER.error(err)

    def handle_textDocument_semanticTokens_full(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_semanticTokens_full")
        LOGGER.debug(message)

    def handle_textDocument_documentLink(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_documentLink")
        LOGGER.debug(message)

    def handle_textDocument_documentSymbol(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_documentSymbol")
        LOGGER.debug(message)

    def handle_textDocument_codeAction(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_codeAction")
        LOGGER.debug(message)

        if message.error:
            LOGGER.error(message.error)
            return

        ACTIVE_DOCUMENT.show_code_action(message.result)

    def handle_textDocument_publishDiagnostics(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_publishDiagnostics")

        LOGGER.debug(message)
        if message.error:
            LOGGER.error(message.error)
            return

        params = message.params
        file_name = DocumentURI(params["uri"]).to_path()
        working_version = self.get_document_version(
            file_name, reset=False, increment=False
        )
        # document_version = params.get("version", -1)
        # if document_version < 0:
        #     LOGGER.debug(f"{file_name} not opened")
        #     return

        # if working_version != document_version:
        #     LOGGER.debug(
        #         "incompatible version, "
        #         f"current: {working_version} != expected: {document_version}"
        #     )
        #     return

        diagnostics = params["diagnostics"]
        document = Document(file_name)
        document.clear_diagnostics()

        if not diagnostics:
            return

        document.apply_diagnostics(diagnostics)

    def handle_workspace_configuration(self, message: lsp.RPCMessage):
        LOGGER.info("handle_workspace_configuration")
        LOGGER.debug(message)
        self.transport.respond(lsp.RPCMessage.response(message["id"], result=[{}]))

    def handle_window_workDoneProgress_create(self, message: lsp.RPCMessage):
        LOGGER.info("handle_window_workDoneProgress_create")
        LOGGER.debug(message)
        self.transport.respond(lsp.RPCMessage.response(message["id"], result=""))

    def handle_window_showMessage(self, message: lsp.RPCMessage):
        LOGGER.info("handle_window_showMessage")
        LOGGER.debug(message)

    def handle_window_logMessage(self, message: lsp.RPCMessage):
        LOGGER.info("handle_window_logMessage")
        LOGGER.debug(message)

        print(message.params["message"])

    def handle_S_progress(self, message: lsp.RPCMessage):
        LOGGER.info("handle_S_progress")
        LOGGER.debug(message)

        sublime.status_message(message.params["value"]["message"])

    def _apply_edit_changes(self, edit_changes: Dict[str, dict]):
        LOGGER.info("_apply_edit_changes")

        if not edit_changes:
            LOGGER.debug("nothing changed")
            return

        for file_name, text_changes in edit_changes.items():
            LOGGER.debug("try apply changes to %s", file_name)

            while True:
                if DOCUMENT_CHANGE_SYNC.busy:
                    LOGGER.debug("busy")
                    time.sleep(0.5)
                    continue
                break

            LOGGER.debug("apply changes to: %s", file_name)
            DOCUMENT_CHANGE_SYNC.set_busy()
            document = Document(DocumentURI(file_name).to_path(), force_open=True)

            try:
                document.apply_document_change(text_changes)

            except Exception as err:
                LOGGER.error(err)

            finally:
                DOCUMENT_CHANGE_SYNC.set_finished()

            LOGGER.debug("finish apply to: %s", file_name)

    def handle_workspace_applyEdit(self, message: lsp.RPCMessage):
        LOGGER.info("handle_workspace_applyEdit")

        params = message.params
        try:
            changes = params["edit"]["changes"]
        except Exception as err:
            LOGGER.error(repr(err))
        else:
            try:
                self._apply_edit_changes(changes)
            except Exception as err:
                LOGGER.error("error apply edit_changes: %s", repr(err))

    def handle_client_registerCapability(self, message: lsp.RPCMessage):
        LOGGER.info("handle_client_registerCapability")
        LOGGER.debug(message)

        self.transport.respond(lsp.RPCMessage.response(message["id"], result=""))

    def handle_textDocument_prepareRename(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_prepareRename")
        LOGGER.debug("message: %s", message)

        ACTIVE_DOCUMENT.prepare_rename(message)

    def handle_textDocument_rename(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_rename")
        LOGGER.debug("message: %s", message)

        try:
            changes = message.result["changes"]
        except Exception as err:
            LOGGER.error(repr(err))
        else:
            try:
                self._apply_edit_changes(changes)
            except Exception as err:
                LOGGER.error("error apply edit_changes: %s", repr(err))

    def handle_textDocument_definition(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_definition")
        LOGGER.debug("message: %s", message)
        ACTIVE_DOCUMENT.goto(message.result)

    def handle_textDocument_declaration(self, message: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_declaration")
        LOGGER.debug("message: %s", message)
        ACTIVE_DOCUMENT.goto(message.result)


GOPLS_CLIENT = GoplsClient()


def plugin_loaded():
    settigs_basename = "Go.sublime-settings"
    settings: sublime.Settings = sublime.load_settings(settigs_basename)
    settings.set("index_files", False)
    settings.set("show_definitions", False)
    sublime.save_settings(settigs_basename)


def plugin_unloaded():
    GOPLS_CLIENT.shutdown_server()


def get_project_path(file_name: str):
    if not file_name:
        raise ValueError("invalid file_name: %s" % file_name)

    folders = [
        folder
        for folder in sublime.active_window().folders()
        if file_name.startswith(folder)
    ]
    if not folders:
        return os.path.dirname(file_name)
    return max(folders)


REQUEST_LOCK = threading.Lock()


def pipe(func):
    def wrapper(*args, **kwargs):
        if REQUEST_LOCK.locked():
            return None

        with REQUEST_LOCK:
            return func(*args, **kwargs)

    return wrapper


def valid_source(view: sublime.View) -> bool:
    return view.match_selector(0, "source.go")


def valid_identifier(view: sublime.View, location: int):
    if view.match_selector(location, "string") or view.match_selector(
        location, "comment"
    ):
        return False
    return True


class EventListener(sublime_plugin.EventListener):
    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> Union[CompletionList, None]:

        if not (valid_source(view) and valid_identifier(view, locations[0])):
            return None

        completions = ACTIVE_DOCUMENT.get_completion_result()
        if completions:
            return completions

        thread = threading.Thread(
            target=self.on_query_completions_task, args=(view, locations)
        )
        thread.start()

        ACTIVE_DOCUMENT.hide_completion()
        return None

    @pipe
    def on_query_completions_task(self, view, locations):
        source = view.substr(sublime.Region(0, view.size()))
        file_name = view.file_name()
        row, col = view.rowcol(locations[0])

        try:
            ACTIVE_DOCUMENT.view = view
            GOPLS_CLIENT.textDocument_completion(file_name, row, col)

        except ServerOffline:
            GOPLS_CLIENT.run_server()
            GOPLS_CLIENT.initialize(get_project_path(file_name))
            GOPLS_CLIENT.textDocument_didOpen(file_name, source)

            GOPLS_CLIENT.textDocument_completion(file_name, row, col)

    def on_hover(self, view: sublime.View, point: int, hover_zone: int) -> None:
        if not valid_source(view):
            return

        if not (hover_zone == sublime.HOVER_TEXT and valid_identifier(view, point)):
            # LOGGER.debug("currently only support HOVER_TEXT")
            return

        if point == view.size():
            return

        thread = threading.Thread(target=self.on_hover_text_task, args=(view, point))
        thread.start()

    @pipe
    def on_hover_text_task(self, view, point):
        source = view.substr(sublime.Region(0, view.size()))
        file_name = view.file_name()
        row, col = view.rowcol(point)

        try:
            ACTIVE_DOCUMENT.view = view
            GOPLS_CLIENT.textDocument_hover(file_name, row, col)

        except ServerOffline:
            GOPLS_CLIENT.run_server()
            GOPLS_CLIENT.initialize(get_project_path(file_name))
            GOPLS_CLIENT.textDocument_didOpen(file_name, source)

            GOPLS_CLIENT.textDocument_hover(file_name, row, col)

    def on_load_async(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (valid_source(view) and GOPLS_CLIENT.is_initialized):
            return

        source = view.substr(sublime.Region(0, view.size()))
        try:
            GOPLS_CLIENT.textDocument_didOpen(file_name, source)
            # set current active view
            ACTIVE_DOCUMENT.view = view
        except ServerOffline:
            pass

    def on_reload_async(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (valid_source(view) and GOPLS_CLIENT.is_initialized):
            return

        source = view.substr(sublime.Region(0, view.size()))
        try:
            GOPLS_CLIENT.textDocument_didOpen(file_name, source)
            # set current active view
            ACTIVE_DOCUMENT.view = view
        except ServerOffline:
            pass

    def on_activated_async(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (valid_source(view) and GOPLS_CLIENT.is_initialized):
            return

        # show diagnostic
        document = Document(file_name)
        document.show_diagnostics()

        source = view.substr(sublime.Region(0, view.size()))
        try:
            GOPLS_CLIENT.textDocument_didOpen(file_name, source)
            # set current active view
            ACTIVE_DOCUMENT.view = view
        except ServerOffline:
            pass

    def on_close(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (valid_source(view) and GOPLS_CLIENT.is_initialized):
            return

        try:
            GOPLS_CLIENT.textDocument_didClose(file_name)
            # reset active view
            ACTIVE_DOCUMENT.view = None
        except ServerOffline:
            pass

    def on_post_save_async(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (valid_source(view) and GOPLS_CLIENT.is_initialized):
            return

        try:
            GOPLS_CLIENT.textDocument_didSave(file_name)
            document = Document(file_name)
            document.clear_diagnostics()

        except ServerOffline:
            pass


class TextChangeListener(sublime_plugin.TextChangeListener):
    def on_text_changed_async(self, changes: List[sublime.TextChange]):

        file_name = self.buffer.file_name()
        view = self.buffer.primary_view()

        if not (valid_source(view) and GOPLS_CLIENT.is_initialized):
            return

        LOGGER.info("on_text_changed_async")

        content_changes = []
        for change in changes:
            start: sublime.HistoricPosition = change.a
            end: sublime.HistoricPosition = change.b

            content_changes.append(
                {
                    "range": {
                        "end": {"character": end.col, "line": end.row},
                        "start": {"character": start.col, "line": start.row},
                    },
                    "rangeLength": change.len_utf8,
                    "text": change.str,
                }
            )

        try:
            LOGGER.debug(f"notify change for {file_name}\n{content_changes}")
            GOPLS_CLIENT.cancelRequest()
            GOPLS_CLIENT.textDocument_didChange(file_name, content_changes)
        except ServerOffline:
            pass


class GotoolsDocumentFormattingCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        LOGGER.info("GotoolsDocumentFormattingCommand")

        if valid_source(self.view) and GOPLS_CLIENT.is_initialized:
            try:
                GOPLS_CLIENT.textDocument_formatting(self.view.file_name())
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and GOPLS_CLIENT.is_initialized


class GotoolsCodeActionCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        LOGGER.info("GotoolsDocumentFormattingCommand")

        if valid_source(self.view) and GOPLS_CLIENT.is_initialized:
            location = self.view.sel()[0]
            start_row, start_col = self.view.rowcol(location.a)
            end_row, end_col = self.view.rowcol(location.b)

            try:
                GOPLS_CLIENT.textDocument_codeAction(
                    self.view.file_name(), start_row, start_col, end_row, end_col
                )
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and GOPLS_CLIENT.is_initialized


class GotoolsWorkspaceExecCommandCommand(sublime_plugin.TextCommand):
    def run(self, edit, params):
        LOGGER.info("GotoolsWorkspaceExecCommandCommand")

        if valid_source(self.view) and GOPLS_CLIENT.is_initialized:
            try:
                GOPLS_CLIENT.workspace_executeCommand(params)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and GOPLS_CLIENT.is_initialized


class GotoolsRenameCommand(sublime_plugin.TextCommand):
    def run(self, edit, row, col, new_name):
        LOGGER.info("GotoolsRenameCommand")

        if valid_source(self.view) and GOPLS_CLIENT.is_initialized:
            file_name = self.view.file_name()
            try:
                GOPLS_CLIENT.textDocument_rename(file_name, row, col, new_name)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and GOPLS_CLIENT.is_initialized


class GotoolsPrepareRenameCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("GotoolsPrepareRenameCommand")

        if valid_source(self.view) and GOPLS_CLIENT.is_initialized:
            file_name = self.view.file_name()

            if location is None:
                location = self.view.sel()[0].a

            row, col = self.view.rowcol(location)
            try:
                GOPLS_CLIENT.textDocument_prepareRename(file_name, row, col)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and GOPLS_CLIENT.is_initialized


class GotoolsGotoDefinitionCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("GotoolsGotoDefinitionCommand")

        if valid_source(self.view) and GOPLS_CLIENT.is_initialized:
            file_name = self.view.file_name()

            if location is None:
                location = self.view.sel()[0].a

            row, col = self.view.rowcol(location)
            try:
                GOPLS_CLIENT.textDocument_definition(file_name, row, col)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and GOPLS_CLIENT.is_initialized


class GotoolsGotoDeclarationCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("GotoolsGotoDeclarationCommand")

        if valid_source(self.view) and GOPLS_CLIENT.is_initialized:
            file_name = self.view.file_name()

            if location is None:
                location = self.view.sel()[0].a

            row, col = self.view.rowcol(location)
            try:
                GOPLS_CLIENT.textDocument_declaration(file_name, row, col)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and GOPLS_CLIENT.is_initialized


class GotoolsRestartServerCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("GotoolsRestartServerCommand")

        if GOPLS_CLIENT.server_running:
            GOPLS_CLIENT.shutdown_server()

    def is_visible(self):
        return GOPLS_CLIENT.server_running
