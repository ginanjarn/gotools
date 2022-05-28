"""gotools main app"""

import dataclasses
import datetime
import logging
import os
import re
import threading
import time

from collections import OrderedDict
from functools import lru_cache
from itertools import chain
from typing import List, Union, Dict, Iterator, Iterable

import sublime
import sublime_plugin

from .api import lsp
from .api.lsp import StandardIO, ServerOffline, DocumentURI
from .third_party import mistune
from .api import tools


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
    def build_completion(item: Dict[str, object]):
        """build completion item"""

        try:
            trigger = item["label"]
            annotation = item.get("detail", "")
            kind = _KIND_MAP.get(item["kind"], sublime.KIND_AMBIGUOUS)
            text_changes = item["textEdit"]

        except Exception as err:
            raise ValueError(f"error build completion from {item}") from err

        # remove snippet
        # text_changes["newText"] = item["label"]

        additional_text_edits = item.get("additionalTextEdits")
        if additional_text_edits is not None:
            return sublime.CompletionItem.command_completion(
                trigger=trigger,
                command="gotools_apply_completion",
                args={
                    "completion": text_changes,
                    "additional_changes": additional_text_edits,
                },
                annotation=annotation,
                kind=kind,
            )

        # default
        return sublime.CompletionItem(
            trigger=trigger,
            annotation=annotation,
            completion=text_changes["newText"],
            kind=kind,
        )

    @staticmethod
    @lru_cache
    def load_snippets():
        """load snippets"""

        def build_completion(completion):
            return sublime.CompletionItem.snippet_completion(
                trigger=completion["trigger"],
                snippet=completion["contents"],
                annotation=completion.get("annotation", ""),
                kind=sublime.KIND_SNIPPET,
            )

        try:
            path = os.path.join(
                sublime.packages_path(), "gotools", "Go.sublime-completions"
            )
            with open(path, "r") as file:
                # use sublime json decoded
                objects = sublime.decode_value(file.read())

        except Exception as err:
            LOGGER.error(f"load snippets error {err}")
            return []

        try:
            completions = [
                build_completion(completion) for completion in objects["completions"]
            ]
        except Exception as err:
            LOGGER.error(f"parse snippets error {err}")
            return []

        return completions

    @classmethod
    def from_rpc(cls, completion_items: List[dict]):
        """load from rpc"""

        LOGGER.debug("completion_list: %s", completion_items)

        # def sort_by_sortText(item):
        #     st = item.get("sortText", 0)
        #     return int(st)

        # completion_items.sort(key=sort_by_sortText)
        completions = [
            cls.build_completion(completion) for completion in completion_items
        ]

        completions = list(chain(completions, cls.load_snippets()))

        return cls(
            completions=completions if completion_items else [],
            flags=sublime.INHIBIT_WORD_COMPLETIONS
            | sublime.INHIBIT_EXPLICIT_COMPLETIONS
            # | sublime.INHIBIT_REORDER,
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


@dataclasses.dataclass
class DiagnosticItem:
    """diagnostic item"""

    region: sublime.Region
    severity: int
    message: str
    raw_data: Dict[str, object]

    @classmethod
    def from_rpc(cls, view: sublime.View, diagnostic: Dict[str, object], /):
        """from rpc"""
        try:
            range_ = diagnostic["range"]
            start = view.text_point(
                range_["start"]["line"], range_["start"]["character"]
            )
            end = view.text_point(range_["end"]["line"], range_["end"]["character"])

            severity = diagnostic["severity"]
            message = diagnostic["message"]
            region = sublime.Region(start, end)

        except Exception as err:
            raise ValueError(f"error loading diagnostic from rpc: {err}") from err

        return cls(region, severity, message, diagnostic)


class Diagnostics:
    """Diagnostic hold diagnostic data at view"""

    REGION_KEYS = {
        1: "gotools.error",
        2: "gotools.warning",
        3: "gotools.information",
        4: "gotools.hint",
    }

    # Diagnostic severity
    ERROR = 1
    WARNING = 2
    INFO = 3
    HINT = 4

    def __init__(self, file_name: str):
        self.file_name = file_name
        self.window = sublime.active_window()
        self.view = self.window.find_open_file(file_name)
        self.outputpanel_name = f"gotools:{file_name}"

    def set_diagnostics(self, diagnostics: List[dict]):
        """set diagnostic

        * show message
        * apply syntax highlight
        """

        diagnostic_items = [
            DiagnosticItem.from_rpc(self.view, diagnostic) for diagnostic in diagnostics
        ]

        message_holder = []

        for severity in (self.ERROR, self.WARNING, self.INFO, self.HINT):
            filtered_diagnostics = [
                diagnostic
                for diagnostic in diagnostic_items
                if diagnostic.severity == severity
            ]

            # add highlight
            self.add_highlight(severity, filtered_diagnostics)

            # create output message
            messages = [
                self._build_message(diagnostic) for diagnostic in filtered_diagnostics
            ]
            message_holder.extend(messages)

        # create output panel
        self.create_panel("\n".join(message_holder))

    def add_highlight(self, severity: int, diagnostics: Iterable[DiagnosticItem]):
        """add syntax highlight regions"""

        key = self.REGION_KEYS[severity]
        regions = [diagnostic.region for diagnostic in diagnostics]

        self.view.add_regions(
            key=key,
            regions=regions,
            scope="Comment",
            icon="circle" if severity == self.ERROR else "dot",
            flags=(
                sublime.DRAW_NO_FILL
                | sublime.DRAW_NO_OUTLINE
                | sublime.DRAW_SQUIGGLY_UNDERLINE
            ),
        )

    def _build_message(self, diagnostic: DiagnosticItem):
        short_name = os.path.basename(self.view.file_name())
        row, col = self.view.rowcol(diagnostic.region.begin())
        return f"{short_name}:{row+1}:{col+1} {diagnostic.message}"

    def erase_highlight(self):
        """erase highlight"""

        for _, value in self.REGION_KEYS.items():
            self.view.erase_regions(value)

    def create_panel(self, message: str) -> None:
        """create output panel"""

        panel = self.window.create_output_panel(self.outputpanel_name)
        panel.set_read_only(False)
        panel.run_command(
            "append", {"characters": message},
        )

    def show_panel(self) -> None:
        """show output panel"""
        self.window.run_command(
            "show_panel", {"panel": f"output.{self.outputpanel_name}"}
        )

    def destroy_panel(self):
        """destroy output panel"""
        self.window.destroy_output_panel(self.outputpanel_name)


@dataclasses.dataclass
class ChangeItem:
    """text change item"""

    region: sublime.Region
    old_text: str
    new_text: str

    @property
    def cursor_move(self):
        return len(self.new_text) - self.region.size()

    def get_region(self, cursor_move: int = 0):
        """get region with adjusted position to cursor move"""
        return sublime.Region(self.region.a + cursor_move, self.region.b + cursor_move)

    @classmethod
    def from_rpc(cls, view: sublime.View, change: Dict[str, object], /):
        """from rpc"""

        try:
            range_ = change["range"]
            new_text = change["newText"]

            start = view.text_point(
                range_["start"]["line"], range_["start"]["character"]
            )
            end = view.text_point(range_["end"]["line"], range_["end"]["character"])

        except Exception as err:
            raise ValueError(f"error loading changes from rpc: {err}") from err

        region = sublime.Region(start, end)
        old_text = view.substr(region)
        return cls(region, old_text, new_text)


class DocumentChangeLock:
    """Document change lock prevent multiple file changes at same time"""

    def __init__(self):
        self._lock = threading.Lock()

    def locked(self):
        return self._lock.locked()

    def acquire(self):
        self._lock.acquire()

    def release(self):
        try:
            self._lock.release()
        except RuntimeError:
            pass


DOCUMENT_CHANGE_LOCK = DocumentChangeLock()


class GotoolsApplyDocumentChangeCommand(sublime_plugin.TextCommand):
    """apply document change to view"""

    def run(self, edit: sublime.Edit, changes: list):
        LOGGER.info(f"GotoolsApplyDocumentChangeCommand: {changes}")

        list_change_item: List[ChangeItem] = [
            ChangeItem.from_rpc(self.view, change) for change in changes
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

        DOCUMENT_CHANGE_LOCK.release()


class WindowProgress:
    """window progress"""

    def __init__(self):
        self.status_key = "GOTOOLS_STATUS"
        self.busy = False

    def progress_task(self, title, message):
        view = sublime.active_window().active_view()
        while True:
            for spin_char in "◓◑◒◐":
                if not self.busy:
                    return
                view.set_status(self.status_key, f"{spin_char} {title}: {message}")
                time.sleep(0.1)

    def start(self, title: str, message: str, /):
        self.busy = True
        thread = threading.Thread(target=self.progress_task, args=(title, message))
        thread.start()

    def finish(self):
        self.busy = False
        for view in sublime.active_window().views():
            view.erase_status(self.status_key)


WINDOW_PROGRESS = WindowProgress()


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
        completions = completions["items"]
        if not completions:
            return

        try:
            completion_list = CompletionList.from_rpc(completions)
            self._completion_result = completion_list
        except Exception as err:
            LOGGER.error(f"build completion error: {err}")
            return

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

            if open_pre := ActiveDocument._start_pre_code_pattern.match(line):
                line = "<div class='code_block'>%s" % line[open_pre.end() :]
                pre_tag = True

            if closing_pre := ActiveDocument._end_pre_code_pattern.search(line):
                line = "%s</div>" % line[: closing_pre.start()]
                pre_tag = False

            line = line.replace("  ", "&nbsp;&nbsp;")
            line = f"{line}<br />" if pre_tag else line

            yield line

    def show_popup(self, documentation):

        try:
            contents = documentation["contents"]["value"]
            kind = documentation["contents"]["kind"]
            start = documentation["range"]["start"]
            location = self.view.text_point(start["line"], start["character"])

        except Exception as err:
            LOGGER.error(f"show_popup param error: {err}")
            return

        if kind == "markdown":
            contents = mistune.markdown(contents, escape=False)

        style = """
        body {
            font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif,"Apple Color Emoji","Segoe UI Emoji";
            margin: 0.8em;
        }
        code, .code_block {
            background-color: color(var(--background) alpha(0.8));
            font-family: ui-monospace,SFMono-Regular,SF Mono,Menlo,Consolas,Liberation Mono,monospace;
            border-radius: 0.4em;
        }

        code {
            padding: 0 0.2em 0 0.2em;
        }

        .code_block {
            padding: 0.4em;
        }
        
        ol, ul {
            padding-left: 1em;
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

    def apply_text_document_changes(self, document_changes: List[Dict[str, dict]]):
        LOGGER.info("apply_text_document_changes")

        if not document_changes:
            LOGGER.debug("nothing changed")
            return

        mapped_document_changes = OrderedDict()
        for change in document_changes:
            file_name = DocumentURI(change["textDocument"]["uri"]).to_path()
            edits = change["edits"]
            mapped_document_changes[file_name] = edits

        mapped_document_changes.move_to_end(self.view.file_name())

        for file_name, text_changes in mapped_document_changes.items():
            LOGGER.debug("try apply changes to %s", file_name)

            while True:
                if DOCUMENT_CHANGE_LOCK.locked():
                    LOGGER.debug("busy")
                    time.sleep(0.5)
                    continue
                break

            LOGGER.debug("apply changes to: %s", file_name)
            DOCUMENT_CHANGE_LOCK.acquire()
            document = Document(file_name, force_open=True)

            try:
                document.apply_document_change(text_changes)

            except Exception as err:
                LOGGER.error(err)

            finally:
                DOCUMENT_CHANGE_LOCK.release()

            LOGGER.debug("finish apply to: %s", file_name)

    def show_code_action(self, action_params: List[dict]):
        def on_done(index=-1):
            if index > -1:
                action = action_params[index]
                LOGGER.debug(action)

                edit = action.get("edit")
                if edit:
                    documentChanges = edit["documentChanges"]
                    self.apply_text_document_changes(documentChanges)

                command = action.get("command")
                if command:
                    self.view.run_command(
                        "gotools_workspace_exec_command", {"params": command},
                    )

        items = [item["title"] for item in action_params]
        self.window.show_quick_panel(items, on_done)

    def apply_document_formatting(self, changes: List[dict]):
        self.view.run_command("gotools_apply_document_change", {"changes": changes})

    def prepare_rename(self, params):
        start = params["range"]["start"]
        end = params["range"]["end"]
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
            try:
                file_name = DocumentURI(location["uri"]).to_path()
                start = location["range"]["start"]
                row, col = start["line"] + 1, start["character"] + 1

            except Exception as err:
                LOGGER.error(f"get location error: {err}")
            else:
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

        self.window: sublime.Window = sublime.active_window()
        self.file_name = file_name
        self.view: sublime.View = self.window.find_open_file(file_name)

        if force_open:
            self.view: sublime.View = self.window.open_file(file_name)

        if self.view is None:
            raise ValueError(f"unable get view for {file_name}")

    def focus_view(self):
        self.window.focus_view(self.view)

    def apply_document_change(self, changes: List[dict]):

        # wait until view loaded
        while True:
            LOGGER.debug("loading %s", self.file_name)
            if self.view.is_loading():
                time.sleep(0.5)
                continue
            break

        self.view.run_command("gotools_apply_document_change", {"changes": changes})

    def apply_diagnostics(self, diagnostics_item: List[dict]):

        if DOCUMENT_CHANGE_LOCK.locked():
            LOGGER.debug("in document change process")
            return

        LOGGER.debug("apply diagnostics to: %s", self.file_name)
        diagnostics = Diagnostics(self.file_name)
        diagnostics.set_diagnostics(diagnostics_item)
        diagnostics.show_panel()

    def clear_diagnostics(self):
        diagnostic = Diagnostics(self.file_name)
        try:
            diagnostic.erase_highlight()
            diagnostic.destroy_panel()

        except Exception as err:
            LOGGER.error(err)

    def show_diagnostics(self):
        diagnostic = Diagnostics(self.file_name)
        diagnostic.show_panel()


ACTIVE_DOCUMENT: ActiveDocument = ActiveDocument()


class GoplsClient(lsp.LSPClient):
    """LSP client listener"""

    def __init__(self):
        super().__init__()
        self.transport: lsp.AbstractTransport = None

        self.completion_commit_character = []
        self.initialize_options = {}

    def run_server(self):
        """run gopls server

        Raises:
            OSError
        """

        sublime.status_message("starting 'gopls'")
        commands = ["gopls"]

        if LOGGER.level == logging.DEBUG:
            commands.extend(["-rpc.trace", "-vv"])

        self.transport = StandardIO(commands)
        self._register_commands()
        self.server_running = True

    def _hide_completion(self, character: str):
        LOGGER.info("_hide_completion")

        if character in ";:!]})":
            ACTIVE_DOCUMENT.hide_completion()

    def shutdown_server(self):
        LOGGER.debug("shutdown_server")
        if self.server_running:
            self.reset_session()

            sublime.status_message("'gopls' terminated")

    def handle_initialize(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_initialize: {message}")

        if message.error:
            LOGGER.error(f"error: {message.error}")
            return

        if not message.result:
            return

        # capabilities = message.result["capabilities"]

        # notify if initialized
        self.initialized()

        file_name = ACTIVE_DOCUMENT.view.file_name()
        source = ACTIVE_DOCUMENT.view.substr(
            sublime.Region(0, ACTIVE_DOCUMENT.view.size())
        )
        self.textDocument_didOpen(file_name, source)

    def handle_textDocument_completion(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_completion: {message}")

        if message.error:
            LOGGER.error(f"error: {message.error}")
            return

        if not message.result:
            return

        ACTIVE_DOCUMENT.show_completions(message.result)

    def handle_textDocument_hover(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_hover: {message}")

        if message.error:
            LOGGER.error(f"error: {message.error}")
            return

        if not message.result:
            return

        ACTIVE_DOCUMENT.show_popup(message.result)

    def handle_textDocument_formatting(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_formatting: {message}")

        if message.error:
            LOGGER.error(f"error: {message.error}")
            return

        if not message.result:
            return

        changes = message.result
        try:
            ACTIVE_DOCUMENT.apply_document_formatting(changes)
        except Exception as err:
            LOGGER.error(err)

    def handle_textDocument_semanticTokens_full(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_semanticTokens_full: {message}")

    def handle_textDocument_documentLink(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_documentLink: {message}")

    def handle_textDocument_documentSymbol(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_documentSymbol: {message}")

    def handle_textDocument_codeAction(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_codeAction: {message}")

        if message.error:
            LOGGER.error(f"error: {message.error}")
            return

        if not message.result:
            return

        ACTIVE_DOCUMENT.show_code_action(message.result)

    def handle_textDocument_publishDiagnostics(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_publishDiagnostics: {message}")

        params = message.params
        file_name = DocumentURI(params["uri"]).to_path()
        # working_version = self.get_document_version(
        #     file_name, reset=False, increment=False
        # )
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

        if not diagnostics:
            document.clear_diagnostics()
            return

        document.apply_diagnostics(diagnostics)

    def handle_workspace_configuration(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_workspace_configuration: {message}")
        self.transport.respond(lsp.RPCMessage.response(message["id"], result=[{}]))

    def handle_window_workDoneProgress_create(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_window_workDoneProgress_create: {message}")
        self.transport.respond(lsp.RPCMessage.response(message["id"], result=""))

    def handle_window_showMessage(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_window_showMessage: {message}")

    def handle_window_logMessage(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_window_logMessage: {message}")

        # print log to console
        print(message.params["message"])

    def handle_S_progress(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_S_progress: {message}")

        params = message.params
        try:
            kind = params["value"]["kind"]
            message = params["value"]["message"]

            if kind == "begin":
                title = params["value"]["title"]
                WINDOW_PROGRESS.start(title, message)
            else:
                WINDOW_PROGRESS.finish()
                sublime.status_message(message)

        except Exception as err:
            LOGGER.error(f"error apply progress: {err}", exc_info=True)

    def handle_workspace_applyEdit(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_workspace_applyEdit: {message}")

        params = message.params
        try:
            document_changes = params["edit"]["documentChanges"]
        except Exception as err:
            LOGGER.error(repr(err))
        else:
            try:
                ACTIVE_DOCUMENT.apply_text_document_changes(document_changes)
            except Exception as err:
                LOGGER.error("error apply document_changes: %s", repr(err))

    def handle_client_registerCapability(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_client_registerCapability: {message}")
        self.transport.respond(lsp.RPCMessage.response(message["id"], result=""))

    def handle_client_unregisterCapability(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_client_unregisterCapability: {message}")
        self.transport.respond(lsp.RPCMessage.response(message["id"], result=""))

    def handle_textDocument_prepareRename(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_prepareRename: {message}")

        if message.error:
            LOGGER.error(f"error: {message.error}")
            return

        if not message.result:
            return

        ACTIVE_DOCUMENT.prepare_rename(message.result)

    def handle_textDocument_rename(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_rename: {message}")

        if message.error:
            LOGGER.error(f"error: {message.error}")
            return

        if not message.result:
            return

        try:
            document_changes = message.result["documentChanges"]
        except Exception as err:
            LOGGER.error(repr(err))
        else:
            try:
                ACTIVE_DOCUMENT.apply_text_document_changes(document_changes)
            except Exception as err:
                LOGGER.error("error apply document_changes: %s", repr(err))

    def handle_textDocument_definition(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_definition: {message}")

        if message.error:
            LOGGER.error(f"error: {message.error}")
            return

        if not message.result:
            return

        ACTIVE_DOCUMENT.goto(message.result)

    def handle_textDocument_declaration(self, message: lsp.RPCMessage):
        LOGGER.info(f"handle_textDocument_declaration: {message}")

        if message.error:
            LOGGER.error(f"error: {message.error}")
            return

        if not message.result:
            return

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


class CancelRunServer:
    """Cancel run server handler"""

    def __init__(self):
        self.next_check = None
        self.exp_base = 1

    def reset(self):
        self.next_check = None
        self.exp_base = 1

    def is_canceled(self):
        if not self.next_check:
            return False
        if datetime.datetime.now() >= self.next_check:
            return False
        if self.exp_base > 5:
            self.reset()
        return True

    def cancel(self):
        delay = 10 ** self.exp_base
        self.exp_base += 1
        self.next_check = datetime.datetime.now() + datetime.timedelta(seconds=delay)
        LOGGER.debug(f"next request at {self.next_check}")


CANCEL_RUN_SERVER = CancelRunServer()


class EventListener(sublime_plugin.EventListener):
    """sublime event listener"""

    def _run_server(self, project_path):
        CANCEL_RUN_SERVER.cancel()
        try:
            GOPLS_CLIENT.run_server()
        except Exception as err:
            LOGGER.error(f"run server error: {err}")
            sublime.status_message(f"run server error: {err}")
        else:
            CANCEL_RUN_SERVER.reset()
            GOPLS_CLIENT.initialize(project_path)

    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> Union[CompletionList, None]:

        if not valid_source(view):
            return None

        if not valid_identifier(view, locations[0]):
            return CompletionList(
                completions=[], flags=sublime.INHIBIT_EXPLICIT_COMPLETIONS
            )

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
        file_name = view.file_name()
        row, col = view.rowcol(locations[0])

        try:
            ACTIVE_DOCUMENT.view = view
            GOPLS_CLIENT.textDocument_completion(file_name, row, col)

        except ServerOffline:
            # delay for next run server
            if CANCEL_RUN_SERVER.is_canceled():
                LOGGER.debug("run_server canceled")
                return
            self._run_server(get_project_path(file_name))

    def on_hover(self, view: sublime.View, point: int, hover_zone: int) -> None:
        if not valid_source(view):
            return

        if not hover_zone == sublime.HOVER_TEXT:
            # LOGGER.debug("currently only support HOVER_TEXT")
            return

        if not valid_identifier(view, point):
            return

        text: str = view.substr(view.word(point))
        if not text.isidentifier():
            return

        if point == view.size():
            return

        thread = threading.Thread(target=self.on_hover_text_task, args=(view, point))
        thread.start()

    @pipe
    def on_hover_text_task(self, view, point):
        file_name = view.file_name()
        row, col = view.rowcol(point)

        try:
            ACTIVE_DOCUMENT.view = view
            GOPLS_CLIENT.textDocument_hover(file_name, row, col)

        except ServerOffline:
            # delay for next run server
            if CANCEL_RUN_SERVER.is_canceled():
                LOGGER.debug("run_server canceled")
                return
            self._run_server(get_project_path(file_name))

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
        finally:
            Diagnostics(file_name).destroy_panel()

    def on_pre_save_async(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (valid_source(view) and GOPLS_CLIENT.is_initialized):
            return

        # view not modified
        if not view.is_dirty():
            return

        try:
            GOPLS_CLIENT.textDocument_didSave(file_name)
        except ServerOffline:
            pass


class TextChangeListener(sublime_plugin.TextChangeListener):
    def on_text_changed(self, changes: List[sublime.TextChange]):

        view = self.buffer.primary_view()

        if not view.file_name():
            return

        if not (GOPLS_CLIENT.is_initialized and valid_source(view)):
            return

        LOGGER.info("on_text_changed_async")
        content_changes = [self.build_change(change) for change in changes]

        try:
            file_name = self.buffer.file_name()
            LOGGER.debug(f"notify change for {file_name}\n{content_changes}")

            # GOPLS_CLIENT.cancelRequest()
            GOPLS_CLIENT.textDocument_didChange(file_name, content_changes)
        except ServerOffline:
            pass

    @staticmethod
    def build_change(change: sublime.TextChange):
        start: sublime.HistoricPosition = change.a
        end: sublime.HistoricPosition = change.b

        return {
            "range": {
                "end": {"character": end.col, "line": end.row},
                "start": {"character": start.col, "line": start.row},
            },
            "rangeLength": change.len_utf8,
            "text": change.str,
        }


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
        LOGGER.info("GotoolsCodeActionCommand")

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


class GotoolsInstallToolsCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("GotoolsInstallToolsCommand")
        thread = threading.Thread(target=tools.install_tools)
        thread.start()
