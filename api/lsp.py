"""LSP implementation"""

import json
import logging
import os
import queue
import re
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import List, Optional, Any
from urllib.parse import urlparse, urlunparse, quote, unquote
from urllib.request import pathname2url, url2pathname

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)


class InvalidMessage(ValueError):
    """message not comply to jsonrpc 2.0 specification"""


class ContentIncomplete(ValueError):
    """expected size less than defined"""


class ContentOverflow(ValueError):
    """expected size greater than defined"""


class ServerOffline(Exception):
    """server offline"""


class NotInitialized(Exception):
    """server not initialized"""


class DocumentURI(str):
    """document uri"""

    @classmethod
    def from_path(cls, file_name):
        """from file name"""
        return cls(urlunparse(("file", "", quote(pathname2url(file_name)), "", "", "")))

    def to_path(self) -> str:
        """convert to path"""
        return url2pathname(unquote(urlparse(self).path))


def path_to_uri(path: str):
    return DocumentURI.from_path(path)


def uri_to_path(uri: str):
    return DocumentURI(uri).to_path()


class RPCMessage(dict):
    """rpc message"""

    JSONRPC_VERSION = "2.0"
    CONTENT_ENCODING = "utf-8"

    def __init__(self, mapping=None, **kwargs):
        super().__init__(kwargs)
        if mapping:
            self.update(mapping)
        # set jsonrpc version
        self["jsonrpc"] = self.JSONRPC_VERSION

    @classmethod
    def from_str(cls, s: str, /):
        return cls(json.loads(s))

    def to_bytes(self) -> bytes:
        message_str = json.dumps(self)
        message_encoded = message_str.encode(self.CONTENT_ENCODING)
        return message_encoded

    @classmethod
    def from_bytes(cls, b: bytes, /):
        try:
            message_str = b.decode(cls.CONTENT_ENCODING)
            message = json.loads(message_str)

            if message["jsonrpc"] != cls.JSONRPC_VERSION:
                raise ValueError("invalid jsonrpc version")

        except Exception as err:
            raise InvalidMessage(err) from err
        else:
            return cls(message)

    @classmethod
    def notification(cls, method, params):
        return cls({"method": method, "params": params})

    @classmethod
    def request(cls, id_, method, params):
        return cls({"id": id_, "method": method, "params": params})

    @classmethod
    def response(cls, id_, result=None, error=None):
        c = cls({"id": id_})
        if result is not None:
            c["result"] = result
        if error is not None:
            c["error"] = error
        return c


class Stream:
    r"""stream object

    This class handle JSONRPC stream format
        '<header>\r\n<content>'
    
    Header items must seperated by '\r\n'
    """

    HEADER_ENCODING = "ascii"

    def __init__(self, content: bytes = b""):
        self.buffer = [content] if content else []
        self._lock = threading.Lock()

    def put(self, data: bytes) -> None:
        """put stream data"""
        with self._lock:
            self.buffer.append(data)

    _content_length_pattern = re.compile(r"^Content-Length: (\d+)", flags=re.MULTILINE)

    def _get_content_length(self, headers: bytes) -> int:
        """get Content-Length"""

        if found := self._content_length_pattern.search(
            headers.decode(self.HEADER_ENCODING)
        ):
            return int(found.group(1))
        raise ValueError("unable find Content-Length")

    def get_content(self) -> bytes:
        """read stream data

        Returns
        ------
        content: bytes

        Raises:
        -------
        InvalidMessage
        EOFError
        ContentIncomplete
        """

        with self._lock:

            buffers = b"".join(self.buffer)
            separator = b"\r\n\r\n"

            if not buffers:
                raise EOFError("buffer empty")

            try:
                header_end = buffers.index(separator)
                content_length = self._get_content_length(buffers[:header_end])

            except ValueError as err:
                # clean up buffer
                self.buffer = []

                LOGGER.error(err)
                LOGGER.debug("buffer: %s", buffers)
                raise InvalidMessage(f"header error: {repr(err)}") from err

            start_index = header_end + len(separator)
            end_index = start_index + content_length
            content = buffers[start_index:end_index]
            recv_len = len(content)

            if recv_len < content_length:
                raise ContentIncomplete(f"want: {content_length}, expected: {recv_len}")

            # replace buffer
            self.buffer = [buffers[end_index:]]
            return content

    @staticmethod
    def wrap_content(content: bytes):
        header = f"Content-Length: {len(content)}"
        return b"\r\n\r\n".join([header.encode(Stream.HEADER_ENCODING), content])


class AbstractTransport(ABC):
    """abstract transport"""

    @abstractmethod
    def run_server(self, command_list: List[str]):
        """run server"""

    @abstractmethod
    def is_running(self):
        """check if server is running"""

    @abstractmethod
    def get_channel(self) -> queue.Queue:
        """transport channel"""

    @abstractmethod
    def send_message(self, message: RPCMessage):
        """send message"""

    @abstractmethod
    def listen(self):
        """listen server message"""

    @abstractmethod
    def terminate(self):
        """terminate"""


class Commands:
    """commands interface"""

    def __init__(self, transport: AbstractTransport):
        self.transport = transport
        self.current_req_id = 0
        self.request_map = {}

    def next_request_id(self):
        self.current_req_id += 1
        return self.current_req_id

    def send_request(self, method: str, params: Any):
        request_id = self.next_request_id()
        message = RPCMessage.request(request_id, method, params)

        if self.request_map:
            # cancel all previous request
            request_map_c = self.request_map.copy()
            for req_id, req_method in request_map_c.items():
                if req_method == method:
                    self.cancel_request(req_id)

        self.request_map[request_id] = method
        self.transport.send_message(message)

    def cancel_request(self, request_id: int):
        del self.request_map[request_id]
        self.send_notification("$/cancelRequest", {"id": request_id})

    def send_response(
        self, request_id: int, result: Optional[Any] = None, error: Optional[Any] = None
    ):
        self.transport.send_message(RPCMessage.response(request_id, result, error))

    def send_notification(self, method: str, params: Any):
        self.transport.send_message(RPCMessage.notification(method, params))

    def initialize(
        self,
        project_path: str,
        client_name: str = "TextEditor",
        client_version: str = "1.0",
    ):
        """initialize server"""

        LOGGER.info("initialize")

        params = {
            "processId": 2372,
            "clientInfo": {"name": client_name, "version": client_version},
            "locale": "en-us",
            "rootPath": project_path,
            "rootUri": DocumentURI.from_path(project_path),
            "capabilities": {
                "workspace": {
                    "applyEdit": True,
                    "workspaceEdit": {
                        "documentChanges": True,
                        "resourceOperations": ["create", "rename", "delete"],
                        "failureHandling": "textOnlyTransactional",
                        "normalizesLineEndings": True,
                        "changeAnnotationSupport": {"groupsOnLabel": True},
                    },
                    "configuration": True,
                    "didChangeWatchedFiles": {
                        "dynamicRegistration": True,
                        "relativePatternSupport": True,
                    },
                    "symbol": {
                        "dynamicRegistration": True,
                        "symbolKind": {
                            "valueSet": [
                                1,
                                2,
                                3,
                                4,
                                5,
                                6,
                                7,
                                8,
                                9,
                                10,
                                11,
                                12,
                                13,
                                14,
                                15,
                                16,
                                17,
                                18,
                                19,
                                20,
                                21,
                                22,
                                23,
                                24,
                                25,
                                26,
                            ]
                        },
                        "tagSupport": {"valueSet": [1]},
                        "resolveSupport": {"properties": ["location.range"]},
                    },
                    "codeLens": {"refreshSupport": True},
                    "executeCommand": {"dynamicRegistration": True},
                    "didChangeConfiguration": {"dynamicRegistration": True},
                    "workspaceFolders": True,
                    "semanticTokens": {"refreshSupport": True},
                    "fileOperations": {
                        "dynamicRegistration": True,
                        "didCreate": True,
                        "didRename": True,
                        "didDelete": True,
                        "willCreate": True,
                        "willRename": True,
                        "willDelete": True,
                    },
                    "inlineValue": {"refreshSupport": True},
                    "inlayHint": {"refreshSupport": True},
                    "diagnostics": {"refreshSupport": True},
                },
                "textDocument": {
                    "publishDiagnostics": {
                        "relatedInformation": True,
                        "versionSupport": False,
                        "tagSupport": {"valueSet": [1, 2]},
                        "codeDescriptionSupport": True,
                        "dataSupport": True,
                    },
                    "synchronization": {
                        "dynamicRegistration": True,
                        "willSave": True,
                        "willSaveWaitUntil": True,
                        "didSave": True,
                    },
                    "completion": {
                        "dynamicRegistration": True,
                        "contextSupport": True,
                        "completionItem": {
                            # "snippetSupport": True,
                            "snippetSupport": False,  # accept text only
                            "commitCharactersSupport": True,
                            "documentationFormat": ["markdown", "plaintext"],
                            "deprecatedSupport": True,
                            "preselectSupport": True,
                            "tagSupport": {"valueSet": [1]},
                            "insertReplaceSupport": True,
                            "resolveSupport": {
                                "properties": [
                                    "documentation",
                                    "detail",
                                    "additionalTextEdits",
                                ]
                            },
                            # "insertTextModeSupport": {"valueSet": [1, 2]},
                            "insertTextModeSupport": {
                                "valueSet": [1]
                            },  # accept text only
                            "labelDetailsSupport": True,
                        },
                        "insertTextMode": 2,
                        "completionItemKind": {
                            "valueSet": [
                                1,
                                2,
                                3,
                                4,
                                5,
                                6,
                                7,
                                8,
                                9,
                                10,
                                11,
                                12,
                                13,
                                14,
                                15,
                                16,
                                17,
                                18,
                                19,
                                20,
                                21,
                                22,
                                23,
                                24,
                                25,
                            ]
                        },
                        "completionList": {
                            "itemDefaults": [
                                "commitCharacters",
                                "editRange",
                                "insertTextFormat",
                                "insertTextMode",
                            ]
                        },
                    },
                    "hover": {
                        "dynamicRegistration": True,
                        "contentFormat": ["markdown", "plaintext"],
                    },
                    "signatureHelp": {
                        "dynamicRegistration": True,
                        "signatureInformation": {
                            "documentationFormat": ["markdown", "plaintext"],
                            "parameterInformation": {"labelOffsetSupport": True},
                            "activeParameterSupport": True,
                        },
                        "contextSupport": True,
                    },
                    "definition": {"dynamicRegistration": True, "linkSupport": True},
                    "references": {"dynamicRegistration": True},
                    "documentHighlight": {"dynamicRegistration": True},
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "symbolKind": {
                            "valueSet": [
                                1,
                                2,
                                3,
                                4,
                                5,
                                6,
                                7,
                                8,
                                9,
                                10,
                                11,
                                12,
                                13,
                                14,
                                15,
                                16,
                                17,
                                18,
                                19,
                                20,
                                21,
                                22,
                                23,
                                24,
                                25,
                                26,
                            ]
                        },
                        "hierarchicalDocumentSymbolSupport": True,
                        "tagSupport": {"valueSet": [1]},
                        "labelSupport": True,
                    },
                    "codeAction": {
                        "dynamicRegistration": True,
                        "isPreferredSupport": True,
                        "disabledSupport": True,
                        "dataSupport": True,
                        "resolveSupport": {"properties": ["edit"]},
                        "codeActionLiteralSupport": {
                            "codeActionKind": {
                                "valueSet": [
                                    "",
                                    "quickfix",
                                    "refactor",
                                    "refactor.extract",
                                    "refactor.inline",
                                    "refactor.rewrite",
                                    "source",
                                    "source.organizeImports",
                                ]
                            }
                        },
                        "honorsChangeAnnotations": False,
                    },
                    "codeLens": {"dynamicRegistration": True},
                    "formatting": {"dynamicRegistration": True},
                    "rangeFormatting": {"dynamicRegistration": True},
                    "onTypeFormatting": {"dynamicRegistration": True},
                    "rename": {
                        "dynamicRegistration": True,
                        "prepareSupport": True,
                        "prepareSupportDefaultBehavior": 1,
                        "honorsChangeAnnotations": True,
                    },
                    "documentLink": {
                        "dynamicRegistration": True,
                        "tooltipSupport": True,
                    },
                    "typeDefinition": {
                        "dynamicRegistration": True,
                        "linkSupport": True,
                    },
                    "implementation": {
                        "dynamicRegistration": True,
                        "linkSupport": True,
                    },
                    "colorProvider": {"dynamicRegistration": True},
                    "foldingRange": {
                        "dynamicRegistration": True,
                        "rangeLimit": 5000,
                        "lineFoldingOnly": True,
                        "foldingRangeKind": {
                            "valueSet": ["comment", "imports", "region"]
                        },
                        "foldingRange": {"collapsedText": False},
                    },
                    "declaration": {"dynamicRegistration": True, "linkSupport": True},
                    "selectionRange": {"dynamicRegistration": True},
                    "callHierarchy": {"dynamicRegistration": True},
                    "semanticTokens": {
                        "dynamicRegistration": True,
                        "tokenTypes": [
                            "namespace",
                            "type",
                            "class",
                            "enum",
                            "interface",
                            "struct",
                            "typeParameter",
                            "parameter",
                            "variable",
                            "property",
                            "enumMember",
                            "event",
                            "function",
                            "method",
                            "macro",
                            "keyword",
                            "modifier",
                            "comment",
                            "string",
                            "number",
                            "regexp",
                            "operator",
                            "decorator",
                        ],
                        "tokenModifiers": [
                            "declaration",
                            "definition",
                            "readonly",
                            "static",
                            "deprecated",
                            "abstract",
                            "async",
                            "modification",
                            "documentation",
                            "defaultLibrary",
                        ],
                        "formats": ["relative"],
                        "requests": {"range": True, "full": {"delta": True}},
                        "multilineTokenSupport": False,
                        "overlappingTokenSupport": False,
                        "serverCancelSupport": True,
                        "augmentsSyntaxTokens": True,
                    },
                    "linkedEditingRange": {"dynamicRegistration": True},
                    "typeHierarchy": {"dynamicRegistration": True},
                    "inlineValue": {"dynamicRegistration": True},
                    "inlayHint": {
                        "dynamicRegistration": True,
                        "resolveSupport": {
                            "properties": [
                                "tooltip",
                                "textEdits",
                                "label.tooltip",
                                "label.location",
                                "label.command",
                            ]
                        },
                    },
                    "diagnostic": {
                        "dynamicRegistration": True,
                        "relatedDocumentSupport": False,
                    },
                },
                "window": {
                    "showMessage": {
                        "messageActionItem": {"additionalPropertiesSupport": True}
                    },
                    "showDocument": {"support": True},
                    "workDoneProgress": True,
                },
                "general": {
                    "staleRequestSupport": {
                        "cancel": True,
                        "retryOnContentModified": [
                            "textDocument/semanticTokens/full",
                            "textDocument/semanticTokens/range",
                            "textDocument/semanticTokens/full/delta",
                        ],
                    },
                    "regularExpressions": {"engine": "ECMAScript", "version": "ES2020"},
                    "markdown": {"parser": "marked", "version": "1.1.0"},
                    "positionEncodings": ["utf-16"],
                },
                "notebookDocument": {
                    "synchronization": {
                        "dynamicRegistration": True,
                        "executionSummarySupport": True,
                    }
                },
            },
            "initializationOptions": {},
            "trace": "off",
            "workspaceFolders": [
                {
                    "uri": DocumentURI.from_path(project_path),
                    "name": os.path.basename(project_path),
                }
            ],
        }

        self.send_request("initialize", params)

    def initialized(self):
        LOGGER.info("initialized")
        params = {}
        self.send_notification("initialized", params)

    def textDocument_didOpen(self, file_name: str, source: str, version: int):
        LOGGER.info("textDocument_didOpen")

        params = {
            "textDocument": {
                "languageId": "go",
                "text": source,
                "uri": path_to_uri(file_name),
                "version": version,
            }
        }
        self.send_notification("textDocument/didOpen", params)

    def _hide_completion(self, characters: str):
        pass

    def textDocument_didChange(self, file_name: str, changes: List[dict], version: int):
        LOGGER.info("textDocument_didChange")

        params = {
            "contentChanges": changes,
            "textDocument": {"uri": path_to_uri(file_name), "version": version,},
        }
        self.send_notification("textDocument/didChange", params)

    def textDocument_didClose(self, file_name: str):
        LOGGER.info("textDocument_didClose")

        params = {"textDocument": {"uri": path_to_uri(file_name)}}
        self.send_notification("textDocument/didClose", params)

    def textDocument_didSave(self, file_name: str):
        LOGGER.info("textDocument_didSave")

        params = {"textDocument": {"uri": path_to_uri(file_name)}}
        self.send_notification("textDocument/didSave", params)

    def textDocument_completion(self, file_name: str, row: int, col: int):
        LOGGER.info("textDocument_completion")

        params = {
            "context": {"triggerKind": 1},  # TODO: adapt KIND
            "position": {"character": col, "line": row},
            "textDocument": {"uri": path_to_uri(file_name)},
        }
        self.send_request("textDocument/completion", params)

    def textDocument_hover(self, file_name: str, row: int, col: int):
        LOGGER.info("textDocument_hover")
        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": path_to_uri(file_name)},
        }
        self.send_request("textDocument/hover", params)

    def textDocument_formatting(self, file_name, tab_size=2):
        LOGGER.info("textDocument_formatting")

        params = {
            "options": {"insertSpaces": True, "tabSize": tab_size},
            "textDocument": {"uri": path_to_uri(file_name)},
        }
        self.send_request("textDocument/formatting", params)

    def textDocument_semanticTokens_full(self, file_name: str):
        LOGGER.info("textDocument_semanticTokens_full")

        params = {"textDocument": {"uri": path_to_uri(file_name)}}
        self.send_request("textDocument/semanticTokens/full", params)

    def textDocument_documentLink(self, file_name: str):
        LOGGER.info("textDocument_documentLink")

        params = {"textDocument": {"uri": path_to_uri(file_name)}}
        self.send_request("textDocument/documentLink", params)

    def textDocument_documentSymbol(self, file_name: str):
        LOGGER.info("textDocument_documentSymbol")

        params = {"textDocument": {"uri": path_to_uri(file_name)}}
        self.send_request("textDocument/documentSymbol", params)

    def textDocument_codeAction(
        self,
        file_name: str,
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        diagnostics=None,
    ):
        LOGGER.info("textDocument_codeAction")

        if not diagnostics:
            diagnostics = []

        params = {
            "context": {"diagnostics": diagnostics},
            "range": {
                "end": {"character": end_col, "line": end_line},
                "start": {"character": start_col, "line": start_line},
            },
            "textDocument": {"uri": path_to_uri(file_name)},
        }
        self.send_request("textDocument/codeAction", params)

    def workspace_executeCommand(self, params: dict):
        self.send_request("workspace/executeCommand", params)

    def workspace_didChangeWatchedFiles(self, changes: List[dict]):
        """
        params = {
                    "changes": [{"uri": DocumentURI.from_path(file_name), "type": 1,}]
                }

        type: 
            1 -> Created
            2 -> Changed
            3 -> Deleted
        """
        params = {"changes": changes}
        self.send_notification("workspace/didChangeWatchedFiles", params)

    def textDocument_prepareRename(self, file_name, row, col):

        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": path_to_uri(file_name)},
        }
        self.send_request("textDocument/prepareRename", params)

    def textDocument_rename(self, file_name, row, col, new_name):

        params = {
            "newName": new_name,
            "position": {"character": col, "line": row},
            "textDocument": {"uri": path_to_uri(file_name)},
        }
        self.send_request("textDocument/rename", params)

    def textDocument_definition(self, file_name, row, col):

        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": path_to_uri(file_name)},
        }
        self.send_request("textDocument/definition", params)

    def textDocument_declaration(self, file_name, row, col):

        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": path_to_uri(file_name)},
        }
        self.send_request("textDocument/declaration", params)

    def exit(self):
        self.send_notification("exit", {})


class BaseHandler:
    """base handler define rpc flattened command handler

    every command have to implement single param argument
    with 'handle_*' prefex

      class DummyHandler(BaseHandler):
          def handle_initialize(self, params):
              pass
    """


class LSPClient(Commands):
    """LSP client"""

    def __init__(self, transport: AbstractTransport, handler: BaseHandler, /):
        super().__init__(transport)

        self.transport_channel = self.transport.get_channel()
        self.handler: BaseHandler = handler

    def run_server(self):
        """run server"""
        self.transport.run_server()

        # listen message
        thread = threading.Thread(target=self._listen_message, daemon=True)
        thread.start()

    def server_running(self):
        """check if server is running"""
        return self.transport.is_running()

    def shutdown_server(self):
        """shutdown server"""
        self.transport.terminate()

    def _listen_message(self):
        stream = Stream()
        while True:
            message = self.transport_channel.get()
            if not message:
                return

            try:
                stream.put(message)
                content = stream.get_content()
            except (EOFError, ContentIncomplete):
                pass
            except Exception as err:
                LOGGER.error(err)

            else:
                message = RPCMessage.from_bytes(content)
                LOGGER.debug(f"Received << {message}")
                try:
                    self.exec_message(message)
                except Exception as err:
                    LOGGER.error(err, exc_info=True)

    def exec_notification(self, method, message):
        LOGGER.info(f"exec notification {message}")
        try:
            self.exec_command(method, message)
        except Exception as err:
            LOGGER.error(err, exc_info=True)

    def exec_request(self, id_, method, message):
        LOGGER.info(f"exec request {message}")
        result, error = None, None
        try:
            result = self.exec_command(method, message)
            LOGGER.debug(f"result: {result}")
        except Exception as err:
            LOGGER.error(err, exc_info=True)
            error = {"code": 9001, "message": str(err)}
        finally:
            self.send_response(id_, result=result, error=error)

    def exec_response(self, message: RPCMessage):
        LOGGER.info(f"exec response {message}")
        try:
            method = self.request_map.pop(message["id"])
        except KeyError as err:
            if error := message.get("error"):
                LOGGER.info(error["message"])
                return
            raise InvalidMessage(f"invalid response 'id': {err}")

        try:
            self.exec_command(method, message)
        except Exception as err:
            LOGGER.error(err, exc_info=True)

    def exec_message(self, message: RPCMessage):
        """exec received message"""

        message_id = message.get("id")
        message_method = message.get("method")
        if message_method:
            params = message.get("params")
            if message_id is not None:
                self.exec_request(message_id, message_method, params)
            else:
                self.exec_notification(message_method, params)
        elif message_id is not None:
            self.exec_response(message)
        else:
            LOGGER.error(f"invalid message: {message}")

    @staticmethod
    def flatten_method(method: str):
        flat_method = method.lower().replace("/", "_").replace("$", "s")
        return f"handle_{flat_method}"

    def exec_command(self, method: str, params: RPCMessage):
        try:
            func = getattr(self.handler, self.flatten_method(method))
        except AttributeError:
            raise InvalidMessage(f"method not found {repr(method)}")

        # exec function
        return func(params)


class StandardIO(AbstractTransport):
    """standard io Transport implementation"""

    BUFFER_LENGTH = 4096

    def __init__(self, executable: str, arguments: List[str]):

        self.server_command = [executable]
        if arguments:
            self.server_command.extend(arguments)

        self.server_process: subprocess.Popen = None

        # set default queue
        self._channel = queue.Queue()

    def get_channel(self) -> queue.Queue:
        return self._channel

    def run_server(self):
        LOGGER.info("run_server")

        command = self.server_command
        startupinfo = None

        if os.name == "nt":
            # if on Windows, hide process window
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW

        LOGGER.debug("command: %s", command)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ,
                bufsize=0,  # no buffering
                startupinfo=startupinfo,
            )
        except FileNotFoundError as err:
            raise FileNotFoundError(f"'{command[0]}' not found in PATH") from err
        except Exception as err:
            raise Exception(f"run server error: {err}") from err

        # listen server message
        self.server_process = process
        self.listen()

    def is_running(self):
        """check if server is running"""

        if not self.server_process:
            return False
        if self.server_process.poll():
            return False

        return True

    def send_message(self, message: RPCMessage):
        LOGGER.debug(f"Send >> {message}")

        if self.server_process is None:
            raise ServerOffline("server not started")

        bmessage = Stream.wrap_content(message.to_bytes())
        try:
            self.server_process.stdin.write(bmessage)
            self.server_process.stdin.flush()

        except OSError as err:
            raise ServerOffline("server has terminated") from err

    def _listen_stdout(self):
        """listen stdout task"""

        while True:
            buf = self.server_process.stdout.read(self.BUFFER_LENGTH)
            self._channel.put(buf)

            if not buf:
                LOGGER.debug("stdout closed")
                return

    def _listen_stderr(self):
        """listen stderr task"""

        while True:
            buf = self.server_process.stderr.read(self.BUFFER_LENGTH)
            if not buf:
                LOGGER.debug("stderr closed")
                return

            try:
                LOGGER.debug("stderr:\n%s", buf)
            except UnicodeDecodeError as err:
                LOGGER.error(err)

    def listen(self):
        """listen PIPE"""
        LOGGER.info("listen")

        stdout_thread = threading.Thread(target=self._listen_stdout, daemon=True)
        stderr_thread = threading.Thread(target=self._listen_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()

    def terminate(self):
        """terminate process"""
        LOGGER.info("terminate")

        if self.is_running():
            self.server_process.terminate()
