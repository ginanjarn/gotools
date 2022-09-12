"""gotools main"""

import logging
import os
import re
import threading
import time

from functools import wraps
from io import StringIO
from pathlib import Path
from typing import List, Iterator

import sublime
import sublime_plugin
from .third_party import mistune

from .api import file_watcher
from .api import lsp
from .api import tools


LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)  # module logging level
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

    def reset_status(self):
        view: sublime.View = sublime.active_window().active_view()
        view.erase_status(self.status_key)

    def show_message(self, message: str):
        window: sublime.Window = sublime.active_window()
        window.status_message(message)


STATUS_MESSAGE = StatusMessage()


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


TEXT_CHANGE_PROCESS = threading.Event()
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


class DiagnosticManager:
    """manage project diagnostic"""

    def __init__(self):
        self.diagnostic_map = {}

    def set(self, view: sublime.View, file_name: str, diagnostics: List[dict]):
        """set diagnostic"""

        self.diagnostic_map[file_name] = diagnostics

        # highlight text
        self.higlight_text(view, diagnostics)
        # show output panel
        self.create_output_panel(file_name, diagnostics)
        self.show_output_panel(file_name)

    def higlight_text(self, view: sublime.View, diagnostics: List[dict]):
        region_key = "gotools_errors"

        # erase current regions
        view.erase_regions(region_key)

        def get_region(diagnostic) -> sublime.Region:
            start = diagnostic["range"]["start"]
            end = diagnostic["range"]["end"]
            start_point = view.text_point_utf16(start["line"], start["character"])
            end_point = view.text_point_utf16(end["line"], end["character"])
            if start_point == end_point:
                return view.line(start_point)
            return sublime.Region(start_point, end_point)

        regions = [get_region(diagnostic) for diagnostic in diagnostics]
        view.add_regions(
            key=region_key,
            regions=regions,
            scope="Comment",
            icon="dot",
            flags=sublime.DRAW_NO_OUTLINE
            | sublime.DRAW_NO_FILL
            | sublime.DRAW_SQUIGGLY_UNDERLINE,
        )

    def create_output_panel(self, file_name: str, diagnostics: List[dict]) -> None:
        """create output panel"""

        def build_message(diagnostic: dict):
            short_name = os.path.basename(file_name)
            row = diagnostic["range"]["start"]["line"]
            col = diagnostic["range"]["start"]["character"]
            message = diagnostic["message"]
            source = diagnostic.get("source", "")

            # natural line index start with 1
            row += 1

            return f"{short_name}:{row}:{col}: {message} ({source})"

        message = "\n".join([build_message(diagnostic) for diagnostic in diagnostics])

        panel_name = f"gotools_panel:{file_name}"
        panel = sublime.active_window().create_output_panel(panel_name)
        panel.set_read_only(False)
        panel.run_command(
            "append", {"characters": message},
        )

    def show_output_panel(self, file_name: str) -> None:
        """show output panel"""

        panel_name = f"gotools_panel:{file_name}"
        sublime.active_window().run_command(
            "show_panel", {"panel": f"output.{panel_name}"}
        )

    def get_diagnostics(self, file_name: str) -> List[dict]:
        """get diagnostics for document"""
        return self.diagnostic_map.get(file_name, [])

    def destroy_output_panel(self, file_name: str):
        """destroy output panel"""

        panel_name = f"gotools_panel:{file_name}"
        sublime.active_window().destroy_output_panel(panel_name)


DIAGNOSTIC_MANAGER = DiagnosticManager()


class ViewNotFoundError(ValueError):
    """view not found in buffer"""


class BufferedDocument:
    """buffered document handler"""

    def __init__(self, view: sublime.View):
        self.view = view
        self._cached_completions = None

    @classmethod
    def from_file(cls, file_name: str):
        """create document from file"""

        view = sublime.active_window().find_open_file(file_name)
        if not view:
            raise ViewNotFoundError(f"{repr(file_name)} not found in buffer")
        return cls(view)

    def save(self):
        self.view.run_command("save", {"async": True})

    def get_cached_completion(self):
        completions = self._cached_completions
        self._cached_completions = None
        return completions

    def file_name(self) -> str:
        return self.view.file_name()

    def source(self) -> str:
        region = sublime.Region(0, self.view.size())
        return self.view.substr(region)

    def set_view(self, view: sublime.View):
        self.view = view

    def get_project_path(self) -> str:
        file_name = self.file_name()
        if not file_name:
            raise ValueError("unable get filename")

        path = Path(file_name)

        # search go.mod up to 5 level parent directory
        for _ in range(5):
            path = path.parent
            if mods := list(path.glob("./go.mod")):
                return str(mods[0].parent)

        raise ValueError("unable get project directory")

    def show_completions(self, completions: List[dict]):
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
        body { margin: 0.8em; font-family: BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }
        code, .code_block {
            background-color: color(var(--background) alpha(0.8));
            font-family: monospace;
            border-radius: 0.4em;
        }
        code { padding: 0 0.2em 0 0.2em; }
        .code_block { padding: 0.4em; }        
        ol, ul { padding-left: 1em; }
        """

        if contents := documentation.get("contents"):
            line = documentation["range"]["start"]["line"]
            character = documentation["range"]["start"]["character"]
            point = self.view.text_point_utf16(line, character)
            kind = contents.get("kind")
            value = contents["value"]

            value = (
                mistune.markdown(value, escape=False) if kind == "markdown" else value
            )
            value = "\n".join(self.adapt_minihtml(value))

            show_popup(f"<style>{style}</style>\n{value}", point)

    def apply_text_changes(self, changes: List[dict]):
        self.view.run_command("gotools_apply_text_changes", {"text_changes": changes})

    def apply_diagnostics(self, diagnostics: List[dict]):
        try:
            DIAGNOSTIC_MANAGER.set(self.view, self.file_name(), diagnostics)
        except Exception as err:
            LOGGER.error(err, exc_info=True)


class Workspace:
    """handle multi document operation"""

    def __init__(self):
        self.watcher = file_watcher.Watcher()

    def set_project_root(self, path: str):
        self.watcher.set_root_folder(path)
        self.watcher.set_glob("*.go")

        # poll available files
        _ = list(self.watcher.poll())

    def open_file(self, file_name: str, source: str = ""):
        GOPLS_CLIENT.textDocument_didOpen(file_name, source)

    def close_file(self, file_name: str):
        GOPLS_CLIENT.textDocument_didClose(file_name)

    @property
    def window(self) -> sublime.Window:
        return sublime.active_window()

    @property
    def view(self) -> sublime.View:
        return self.window.active_view()

    def focus_view(self, view: sublime.View):
        self.window.focus_view(view)

    def notify_file_changes(self):
        def build_item(change: file_watcher.ChangeItem):
            return {
                "uri": lsp.DocumentURI.from_path(change.file_name),
                "type": change.change_type,
            }

        changes = [build_item(change) for change in self.watcher.poll()]

        if changes:
            try:
                GOPLS_CLIENT.workspace_didChangeWatchedFiles(changes)
            except lsp.NotInitialized:
                pass

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

        self.window.show_quick_panel(
            action_titles, on_select=select_action, placeholder="select action"
        )

    def apply_document_changes(self, document_changes: List[dict]):

        active_view = ACTIVE_DOCUMENT.view

        for change in document_changes:
            if TEXT_CHANGE_PROCESS.is_set():
                LOGGER.debug("waiting change process")
                TEXT_CHANGE_SYNC.wait()
                LOGGER.debug("change process done")

            TEXT_CHANGE_PROCESS.set()
            TEXT_CHANGE_SYNC.clear()

            file_name = lsp.DocumentURI(change["textDocument"]["uri"]).to_path()

            try:
                document = BufferedDocument.from_file(file_name)
                document.apply_text_changes(change["edits"])
                document.save()

            except ViewNotFoundError:
                # modify file without buffer
                document = UnbufferedDocument(file_name)
                document.apply_text_changes(change["edits"])
            finally:
                TEXT_CHANGE_PROCESS.clear()

        # focus active view
        self.focus_view(active_view)

    def input_rename(self, file_name: str, row: int, col: int, placeholder: str):
        def rename_callback(new_name):
            GOPLS_CLIENT.textDocument_rename(file_name, row, col, new_name)

        self.window.show_input_panel(
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
        LOGGER.debug(locations)

        def select_location(index=-1):
            if index < 0:
                return
            self.window.open_file(locations[index], flags=sublime.ENCODED_POSITION)

        self.window.show_quick_panel(
            locations, on_select=select_location, placeholder="select location"
        )


ACTIVE_DOCUMENT = BufferedDocument(sublime.View(0))
WORKSPACE = Workspace()


class GoplsHandler(lsp.BaseHandler):
    """gopls command handler"""

    def __init__(self):
        self.progress_token = set()

    def handle_initialize(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.error(error["message"])

        if result := message.get("result"):
            # handle initialize here
            LOGGER.debug(result)

            # notify client has initialized
            GOPLS_CLIENT.initialized()

            # open active document
            WORKSPACE.open_file(ACTIVE_DOCUMENT.file_name(), ACTIVE_DOCUMENT.source())

    def handle_window_workDoneProgress_create(self, message: lsp.RPCMessage):
        message_id = message.get("id")
        self.progress_token.add(message["params"]["token"])
        GOPLS_CLIENT.send_response(message_id, result="")

    def handle_S_progress(self, message: lsp.RPCMessage):
        params = message["params"]
        token = params["token"]

        if token not in self.progress_token:
            raise ValueError("invalid token")

        kind = params["value"]["kind"]
        title = params["value"].get("title")  # end message has not title
        message = params["value"]["message"]

        if kind == "begin":
            STATUS_MESSAGE.set_status(f"{title}: {message}")

        elif kind == "end":
            self.progress_token.remove(token)
            STATUS_MESSAGE.reset_status()
            STATUS_MESSAGE.show_message(message)

    def handle_workspace_configuration(self, message: lsp.RPCMessage):
        message_id = message.get("id")
        GOPLS_CLIENT.send_response(message_id, result=[{}])

    def handle_window_logMessage(self, message: lsp.RPCMessage):
        params = message["params"]
        if message := params.get("message"):
            print(message)

    def handle_client_registerCapability(self, message: lsp.RPCMessage):
        message_id = message.get("id")
        GOPLS_CLIENT.send_response(message_id, result="")

    def handle_textDocument_documentSymbol(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.debug(error["message"])
        if result := message.get("result"):
            LOGGER.debug(result)

    def handle_textDocument_publishDiagnostics(self, message: lsp.RPCMessage):
        params = message["params"]

        diagnostics = params["diagnostics"]
        file_name = lsp.DocumentURI(params["uri"]).to_path()

        try:
            document = BufferedDocument.from_file(file_name)
        except ViewNotFoundError:
            # ignore unbuffered document
            pass
        else:
            try:
                document.apply_diagnostics(diagnostics)
            except Exception as err:
                LOGGER.error(err, exc_info=True)

    def handle_textDocument_codeAction(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.debug(error["message"])

        if result := message.get("result"):
            try:
                WORKSPACE.show_code_actions(result)
            except Exception as err:
                LOGGER.error(err, exc_info=True)

    def handle_textDocument_completion(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.debug(error["message"])

        if result := message.get("result"):
            if items := result.get("items"):
                try:
                    ACTIVE_DOCUMENT.show_completions(items)
                except Exception as err:
                    LOGGER.error(err, exc_info=True)

    def handle_textDocument_hover(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.debug(error["message"])

        if result := message.get("result"):
            try:
                ACTIVE_DOCUMENT.show_documentation(result)
            except Exception as err:
                LOGGER.error(err, exc_info=True)

    def handle_textDocument_formatting(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.debug(error["message"])

        if result := message.get("result"):
            changes = result
            try:
                ACTIVE_DOCUMENT.apply_text_changes(changes)
            except Exception as err:
                LOGGER.error(err, exc_info=True)

    def handle_workspace_executeCommand(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.debug(error["message"])

        if result := message.get("result"):
            LOGGER.debug(result)

    def handle_workspace_applyEdit(self, message: lsp.RPCMessage):
        message_id = message["id"]
        params = message["params"]

        document_changes = params["edit"]["documentChanges"]
        try:
            WORKSPACE.apply_document_changes(document_changes)
        except Exception as err:
            LOGGER.error(err, exc_info=True)
        else:
            GOPLS_CLIENT.send_response(message_id, result={"applied": True})

    def handle_textDocument_prepareRename(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.debug(error["message"])

        if result := message.get("result"):
            srow = result["range"]["start"]["line"]
            scol = result["range"]["start"]["character"]
            placeholder = result["placeholder"]

            file_name = ACTIVE_DOCUMENT.file_name()

            WORKSPACE.input_rename(file_name, srow, scol, placeholder)

    def handle_textDocument_rename(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.debug(error["message"])

        if result := message.get("result"):
            document_changes = result["documentChanges"]
            try:
                WORKSPACE.apply_document_changes(document_changes)
            except Exception as err:
                LOGGER.error(err, exc_info=True)

    def handle_textDocument_definition(self, message: lsp.RPCMessage):
        if error := message.get("error"):
            LOGGER.debug(error["message"])

        if result := message.get("result"):
            LOGGER.debug(f"handle definition: {result}")
            try:
                WORKSPACE.show_definition(result)
            except Exception as err:
                LOGGER.error(err, exc_info=True)


# setup gopls client
transport = lsp.StandardIO("gopls", ["-vv"])
handler = GoplsHandler()
GOPLS_CLIENT = lsp.LSPClient(transport, handler)


class ServerManager:
    """ServerManager manage server lifetime"""

    def __init__(self):
        self.is_running = False

        self.cancel_timeout = 0
        self.factor = 0

    def running(self, func):
        """continue execution if server is running"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            if self.is_running:
                return func(*args, **kwargs)

            LOGGER.info("server offline")
            return None

        return wrapper

    def run_server(self, func):
        """run server if not running"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            if self.is_running:
                return func(*args, **kwargs)

            # run server
            self._run()
            return None

        return wrapper

    def _run(self):
        LOGGER.info("run server")

        # delay next trial if error run server
        now = time.time()
        if 0 < now < self.cancel_timeout:
            LOGGER.debug(f"cancel run server until {repr(time.ctime(now))}")
            return

        try:
            # set timeout
            self.cancel_timeout = now + (10 * self.factor)

            STATUS_MESSAGE.set_status("starting server")
            GOPLS_CLIENT.run_server()

        except Exception as err:
            # increment timeout factor
            self.factor += 1

            if isinstance(err, FileNotFoundError):
                print(f"> {err}. Make sure if 'gopls' is installed!'")
            else:
                LOGGER.error(err, exc_info=True)

            STATUS_MESSAGE.reset_status()
            STATUS_MESSAGE.show_message(f"error starting server: {err}")

        else:
            # reset
            self.cancel_timeout = 0
            self.factor = 0

            self.is_running = True
            self._initialize()

    def _initialize(self):
        LOGGER.info("initialize server")

        try:
            project_path = ACTIVE_DOCUMENT.get_project_path()
        except ValueError as err:
            sublime.error_message(err)
        else:
            WORKSPACE.set_project_root(project_path)
            GOPLS_CLIENT.initialize(project_path, "Sublime Text", sublime.version())

    def shutdown_server(self):
        if self.is_running:
            GOPLS_CLIENT.shutdown_server()
            self.is_running = False


SERVER_MANAGER = ServerManager()


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


class EventListener(sublime_plugin.EventListener):
    """event listener"""

    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ):
        if not valid_source(view):
            return None

        # trigger completion at first location
        point = locations[0]

        if not valid_context(view, point):
            return None

        if completions := ACTIVE_DOCUMENT.get_cached_completion():
            return sublime.CompletionList(
                completions,
                flags=sublime.INHIBIT_WORD_COMPLETIONS
                | sublime.INHIBIT_EXPLICIT_COMPLETIONS,
            )

        ACTIVE_DOCUMENT.set_view(view)
        ACTIVE_DOCUMENT.hide_completion()

        thread = threading.Thread(
            target=self.trigger_completion_task, args=(view, point)
        )
        thread.start()

    @SERVER_MANAGER.run_server
    @SERVER_MANAGER.running
    def trigger_completion_task(self, view: sublime.View, point: int):
        file_name = view.file_name()
        row, col = view.rowcol_utf16(point)

        try:
            GOPLS_CLIENT.textDocument_completion(file_name, row, col)
        except lsp.NotInitialized:
            LOGGER.debug("NotInitialized")
            pass

    def on_hover(self, view: sublime.View, point: int, hover_zone: int):
        if not valid_source(view):
            return

        if not valid_context(view, point):
            return

        ACTIVE_DOCUMENT.set_view(view)

        # currently only apply hover text
        if hover_zone != sublime.HOVER_TEXT:
            return

        thread = threading.Thread(target=self.hover_text_task, args=(view, point))
        thread.start()

    @SERVER_MANAGER.run_server
    @SERVER_MANAGER.running
    def hover_text_task(self, view: sublime.View, point: int):
        file_name = view.file_name()
        row, col = view.rowcol_utf16(point)

        try:
            GOPLS_CLIENT.textDocument_hover(file_name, row, col)
        except lsp.NotInitialized:
            LOGGER.debug("NotInitialized")
            pass

    def _notify_document_open(self, view: sublime.View):
        if not (valid_source(view) and SERVER_MANAGER.is_running):
            return

        WORKSPACE.notify_file_changes()
        source = view.substr(sublime.Region(0, view.size()))

        try:
            WORKSPACE.open_file(view.file_name(), source)
        except lsp.NotInitialized:
            pass

    def on_activated(self, view: sublime.View):
        self._notify_document_open(view)

        if not valid_source(view):
            return

        DIAGNOSTIC_MANAGER.show_output_panel(view.file_name())

    def on_load(self, view: sublime.View):
        self._notify_document_open(view)

    def on_reload(self, view: sublime.View):
        self._notify_document_open(view)

    def on_pre_save(self, view: sublime.View):
        if not (valid_source(view) and SERVER_MANAGER.is_running):
            return

        if not view.is_dirty():
            return

        try:
            GOPLS_CLIENT.textDocument_didSave(view.file_name())
        except lsp.NotInitialized:
            pass

    def on_post_save(self, view: sublime.View):
        if not (valid_source(view) and SERVER_MANAGER.is_running):
            return

        WORKSPACE.notify_file_changes()

    def on_pre_close(self, view: sublime.View):
        if not (valid_source(view) and SERVER_MANAGER.is_running):
            return
        try:
            WORKSPACE.close_file(view.file_name())
        except lsp.NotInitialized:
            pass

        DIAGNOSTIC_MANAGER.destroy_output_panel(view.file_name())


def plugin_unloaded():
    SERVER_MANAGER.shutdown_server()


class TextChangeListener(sublime_plugin.TextChangeListener):
    """listen text change"""

    def on_text_changed(self, changes: List[sublime.TextChange]):

        buffer: sublime.Buffer = self.buffer
        file_name = buffer.file_name()
        view = buffer.primary_view()

        if not (valid_source(view) and SERVER_MANAGER.is_running):
            return

        change_items = list(self.build_items(view, changes))

        try:
            GOPLS_CLIENT.textDocument_didChange(file_name, change_items)
        except lsp.NotInitialized:
            pass

    def build_items(self, view: sublime.View, changes: List[sublime.TextChange]):
        for change in changes:
            start: sublime.HistoricPosition = change.a
            end: sublime.HistoricPosition = change.b

            yield {
                "range": {
                    "end": {"character": end.col, "line": end.row},
                    "start": {"character": start.col, "line": start.row},
                },
                "rangeLength": change.len_utf16,
                "text": change.str,
            }


class GotoolsDocumentFormattingCommand(sublime_plugin.TextCommand):
    """document formatting command"""

    def run(self, edit: sublime.Edit):
        if not (valid_source(self.view) and SERVER_MANAGER.is_running):
            return

        ACTIVE_DOCUMENT.set_view(self.view)

        try:
            GOPLS_CLIENT.textDocument_formatting(self.view.file_name())
        except lsp.NotInitialized:
            pass

    def is_visible(self):
        return valid_source(self.view) and SERVER_MANAGER.is_running


class GotoolsCodeActionCommand(sublime_plugin.TextCommand):
    """code action command"""

    def run(self, edit: sublime.Edit):
        if not (valid_source(self.view) and SERVER_MANAGER.is_running):
            return

        ACTIVE_DOCUMENT.set_view(self.view)

        selection = self.view.sel()[0]
        srow, scol = self.view.rowcol_utf16(selection.a)
        erow, ecol = self.view.rowcol_utf16(selection.b)

        diagnostics = DIAGNOSTIC_MANAGER.get_diagnostics(self.view.file_name())

        try:
            GOPLS_CLIENT.textDocument_codeAction(
                self.view.file_name(), srow, scol, erow, ecol, diagnostics
            )
        except lsp.NotInitialized:
            pass

    def is_visible(self):
        return valid_source(self.view) and SERVER_MANAGER.is_running


class GotoolsRenameCommand(sublime_plugin.TextCommand):
    """code action command"""

    def run(self, edit: sublime.Edit):
        if not (valid_source(self.view) and SERVER_MANAGER.is_running):
            return

        ACTIVE_DOCUMENT.set_view(self.view)

        selection = self.view.sel()[0]
        srow, scol = self.view.rowcol_utf16(selection.a)

        try:
            GOPLS_CLIENT.textDocument_prepareRename(
                self.view.file_name(), srow, scol,
            )
        except lsp.NotInitialized:
            pass

    def is_visible(self):
        return valid_source(self.view) and SERVER_MANAGER.is_running


class GotoolsGotoDefinitionCommand(sublime_plugin.TextCommand):
    """code action command"""

    def run(self, edit: sublime.Edit):
        if not (valid_source(self.view) and SERVER_MANAGER.is_running):
            return

        ACTIVE_DOCUMENT.set_view(self.view)

        selection = self.view.sel()[0]
        srow, scol = self.view.rowcol_utf16(selection.a)

        try:
            GOPLS_CLIENT.textDocument_definition(
                self.view.file_name(), srow, scol,
            )
        except lsp.NotInitialized:
            pass

    def is_visible(self):
        return valid_source(self.view) and SERVER_MANAGER.is_running


class GotoolsRestartServerCommand(sublime_plugin.TextCommand):
    """restart server"""

    def run(self, edit, location=None):
        LOGGER.info("GotoolsRestartServerCommand")
        SERVER_MANAGER.shutdown_server()

    def is_visible(self):
        return SERVER_MANAGER.is_running


class GotoolsInstallToolsCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("GotoolsInstallToolsCommand")
        thread = threading.Thread(target=tools.install_tools)
        thread.start()
