"""LSP implementation"""

import json
import logging
import os
import queue
import re
import subprocess
import threading
from abc import ABC, abstractmethod
from functools import wraps
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse, urlunparse, quote, unquote
from urllib.request import pathname2url, url2pathname

LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)  # module logging level
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


class RPCMessage(dict):
    """rpc message"""

    JSONRPC_VERSION = "2.0"
    HEADER_ENCODING = "ascii"
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

        header = f"Content-Length: {len(message_encoded)}"
        return b"\r\n\r\n".join([header.encode(self.HEADER_ENCODING), message_encoded])

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

    @classmethod
    def cancel_request(cls, id_):
        return cls({"method": "$/cancelRequest", "params": {"id": id_}})

    @property
    def method(self):
        return self.get("method")

    @property
    def params(self):
        return self.get("params")

    @property
    def error(self):
        return self.get("error")

    @property
    def result(self):
        return self.get("result")


class Stream:
    r"""stream object

    This class handle JSONRPC stream format
        '<header>\r\n<content>'
    
    Header items must seperated by '\r\n'
    """

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

        if found := self._content_length_pattern.search(headers.decode("ascii")):
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


class Session:
    """project session"""

    def __init__(self):
        self.is_initialized = False

    def initialized(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if self.is_initialized:
                return func(*args, **kwargs)

            raise NotInitialized("project not initialized")

        return wrapper

    def initialize(self):
        self.is_initialized = True

    def exit(self):
        self.is_initialized = False


# project session
session = Session()


class AbstractTransport(ABC):
    """abstract transport"""

    @abstractmethod
    def run_server(self, command_list: List[str]):
        """run server"""

    @abstractmethod
    def is_running(self):
        """check if server is running"""

    @abstractmethod
    def set_receiver(self, q: queue.Queue):
        """set message receiver"""

    @abstractmethod
    def send_message(self, message: RPCMessage):
        """send message"""

    @abstractmethod
    def listen(self):
        """listen server message"""

    @abstractmethod
    def terminate(self):
        """terminate"""


class Document:
    """manage working document"""

    def __init__(self, file_name: str, version: int = 0):
        self.file_name = file_name
        self.version = version

    def next_version(self) -> int:
        self.version += 1
        return self.version

    def get_uri(self) -> DocumentURI:
        return DocumentURI.from_path(self.file_name)


class Commands:
    """commands interface"""

    def __init__(self, transport: AbstractTransport):
        self.documents: Dict[str, Document] = {}
        self.transport = transport

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

        # set session initialized
        session.initialize()

    @session.initialized
    def textDocument_didOpen(self, file_name: str, source: str):
        LOGGER.info("textDocument_didOpen")

        document = Document(file_name)
        # add document to working documents
        self.documents[file_name] = document

        params = {
            "textDocument": {
                "languageId": "go",
                "text": source,
                "uri": document.get_uri(),
                "version": document.version,
            }
        }
        self.send_notification("textDocument/didOpen", params)

    def _hide_completion(self, characters: str):
        pass

    @session.initialized
    def textDocument_didChange(self, file_name: str, changes: List[dict]):
        LOGGER.info("textDocument_didChange")

        if document := self.documents.get(file_name):
            params = {
                "contentChanges": changes,
                "textDocument": {
                    "uri": document.get_uri(),
                    "version": document.next_version(),
                },
            }
            LOGGER.debug("didChange: %s", params)
            self._hide_completion(changes[0]["text"])
            self.send_notification("textDocument/didChange", params)

    @session.initialized
    def textDocument_didClose(self, file_name: str):
        LOGGER.info("textDocument_didClose")

        if document := self.documents.get(file_name):
            params = {"textDocument": {"uri": document.get_uri()}}
            self.send_notification("textDocument/didClose", params)
            # remove document from working documents
            del self.documents[file_name]


    @session.initialized
    def textDocument_didSave(self, file_name: str):
        LOGGER.info("textDocument_didSave")

        if document := self.documents.get(file_name):
            params = {"textDocument": {"uri": document.get_uri()}}
            self.send_notification("textDocument/didSave", params)

    @session.initialized
    def textDocument_completion(self, file_name: str, row: int, col: int):
        LOGGER.info("textDocument_completion")

        if document := self.documents.get(file_name):
            params = {
                "context": {"triggerKind": 1},  # TODO: adapt KIND
                "position": {"character": col, "line": row},
                "textDocument": {"uri": document.get_uri()},
            }
            self.send_request("textDocument/completion", params)

    @session.initialized
    def textDocument_hover(self, file_name: str, row: int, col: int):
        LOGGER.info("textDocument_hover")

        if document := self.documents.get(file_name):
            params = {
                "position": {"character": col, "line": row},
                "textDocument": {"uri": document.get_uri()},
            }
            self.send_request("textDocument/hover", params)

    @session.initialized
    def textDocument_formatting(self, file_name, tab_size=2):
        LOGGER.info("textDocument_formatting")

        if document := self.documents.get(file_name):
            params = {
                "options": {"insertSpaces": True, "tabSize": tab_size},
                "textDocument": {"uri": document.get_uri()},
            }
            self.send_request("textDocument/formatting", params)

    @session.initialized
    def textDocument_semanticTokens_full(self, file_name: str):
        LOGGER.info("textDocument_semanticTokens_full")

        if document := self.documents.get(file_name):
            params = {"textDocument": {"uri": document.get_uri()}}
            self.send_request("textDocument/semanticTokens/full", params)

    @session.initialized
    def textDocument_documentLink(self, file_name: str):
        LOGGER.info("textDocument_documentLink")

        if document := self.documents.get(file_name):
            params = {"textDocument": {"uri": document.get_uri()}}
            self.send_request("textDocument/documentLink", params)

    @session.initialized
    def textDocument_documentSymbol(self, file_name: str):
        LOGGER.info("textDocument_documentSymbol")

        if document := self.documents.get(file_name):
            params = {"textDocument": {"uri": document.get_uri()}}
            self.send_request("textDocument/documentSymbol", params)

    @session.initialized
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

        if document := self.documents.get(file_name):
            params = {
                "context": {"diagnostics": diagnostics},
                "range": {
                    "end": {"character": end_col, "line": end_line},
                    "start": {"character": start_col, "line": start_line},
                },
                "textDocument": {"uri": document.get_uri()},
            }
            LOGGER.debug("codeAction params: %s", params)
            self.send_request("textDocument/codeAction", params)

    @session.initialized
    def workspace_executeCommand(self, params: dict):
        self.send_request("workspace/executeCommand", params)

    @session.initialized
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

    @session.initialized
    def textDocument_prepareRename(self, file_name, row, col):

        if document := self.documents.get(file_name):
            params = {
                "position": {"character": col, "line": row},
                "textDocument": {"uri": document.get_uri()},
            }
            self.send_request("textDocument/prepareRename", params)

    @session.initialized
    def textDocument_rename(self, file_name, row, col, new_name):

        if document := self.documents.get(file_name):
            params = {
                "newName": new_name,
                "position": {"character": col, "line": row},
                "textDocument": {"uri": document.get_uri()},
            }
            self.send_request("textDocument/rename", params)

    @session.initialized
    def textDocument_definition(self, file_name, row, col):

        if document := self.documents.get(file_name):
            params = {
                "position": {"character": col, "line": row},
                "textDocument": {"uri": document.get_uri()},
            }
            self.send_request("textDocument/definition", params)

    @session.initialized
    def textDocument_declaration(self, file_name, row, col):

        if document := self.documents.get(file_name):
            params = {
                "position": {"character": col, "line": row},
                "textDocument": {"uri": document.get_uri()},
            }
            self.send_request("textDocument/declaration", params)

    def exit(self):
        self.send_notification("exit", {})
        # exit session
        session.exit()


class BaseHandler:
    """base received command handler"""

    def handle_initialize(self, params: RPCMessage):
        """handle initialize"""

    def handle_textDocument_completion(self, params: RPCMessage):
        """handle document completion"""

    def handle_textDocument_hover(self, params: RPCMessage):
        """handle document hover"""

    def handle_textDocument_formatting(self, params: RPCMessage):
        """handle document formatting"""

    def handle_textDocument_semanticTokens_full(self, params: RPCMessage):
        """handle document semantic tokens"""

    def handle_workspace_semanticTokens_refresh(self, params: RPCMessage):
        """handle workspace semanticTokens refresh request"""

    def handle_workspace_applyEdit(self, params: RPCMessage):
        """handle workspace apply edit"""

    def handle_client_registerCapability(self, params: RPCMessage):
        """handle client registerCapability"""

    def handle_client_unregisterCapability(self, params: RPCMessage):
        """handle client unregisterCapability"""

    def handle_textDocument_documentLink(self, params: RPCMessage):
        """handle document link"""

    def handle_textDocument_documentSymbol(self, params: RPCMessage):
        """handle document symbol"""

    def handle_textDocument_codeAction(self, params: RPCMessage):
        """handle document code action"""

    def handle_S_progress(self, params: RPCMessage):
        """handle progress"""

    def handle_textDocument_publishDiagnostics(self, params: RPCMessage):
        """handle publish diagnostic"""

    def handle_workspace_configuration(self, params: RPCMessage):
        """handle workspace configuration"""

    def handle_window_workDoneProgress_create(self, params: RPCMessage):
        """handle work progress done create"""

    def handle_workspace_executeCommand(self, params: RPCMessage):
        """handle workspace executeCommand"""

    def handle_window_showMessage(self, message: RPCMessage):
        """handle show message"""

    def handle_window_logMessage(self, message: RPCMessage):
        """handle log message"""

    def handle_textDocument_prepareRename(self, params: RPCMessage):
        """handle document prepare rename"""

    def handle_textDocument_rename(self, params: RPCMessage):
        """handle document rename"""

    def handle_textDocument_definition(self, params: RPCMessage):
        """handle document definition"""

    def handle_textDocument_declaration(self, params: RPCMessage):
        """handle document definition"""

    def get_command_map(self):
        command_map = {
            "initialize": self.handle_initialize,
            "textDocument/publishDiagnostics": self.handle_textDocument_publishDiagnostics,
            "workspace/configuration": self.handle_workspace_configuration,
            "window/workDoneProgress/create": self.handle_window_workDoneProgress_create,
            "window/showMessage": self.handle_window_showMessage,
            "window/logMessage": self.handle_window_logMessage,
            "textDocument/documentLink": self.handle_textDocument_documentLink,
            "textDocument/hover": self.handle_textDocument_hover,
            "textDocument/completion": self.handle_textDocument_completion,
            "textDocument/formatting": self.handle_textDocument_formatting,
            "textDocument/documentSymbol": self.handle_textDocument_documentSymbol,
            "textDocument/codeAction": self.handle_textDocument_codeAction,
            "$/progress": self.handle_S_progress,
            "textDocument/semanticTokens/full": self.handle_textDocument_semanticTokens_full,
            "workspace/semanticTokens/refresh": self.handle_workspace_semanticTokens_refresh,
            "workspace/applyEdit": self.handle_workspace_applyEdit,
            "workspace/executeCommand": self.handle_workspace_executeCommand,
            "client/registerCapability": self.handle_client_registerCapability,
            "client/unregisterCapability": self.handle_client_unregisterCapability,
            "textDocument/prepareRename": self.handle_textDocument_prepareRename,
            "textDocument/rename": self.handle_textDocument_rename,
            "textDocument/declaration": self.handle_textDocument_declaration,
            "textDocument/definition": self.handle_textDocument_definition,
        }
        return command_map


class LSPClient(Commands):
    """LSP client"""

    def __init__(self, transport: AbstractTransport, handler: BaseHandler, /):

        super().__init__(transport)

        self.message_queue = queue.Queue()
        self.transport.set_receiver(self.message_queue)

        # command handler map
        self.command_map = {}
        self.command_map.update(handler.get_command_map())

        # request
        self.request_map: Dict[int, str] = {}
        self.current_req_id = -1

    def next_request_id(self):
        self.current_req_id += 1
        return self.current_req_id

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
            message = self.message_queue.get()
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
                self.exec_message(message)

    def exec_message(self, message: RPCMessage):
        """exec received message"""

        message_id = message.get("id")
        message_method = message.get("method")

        if message_id is not None and message_id in self.request_map:
            try:
                self.exec_response(message)
            except Exception as err:
                LOGGER.error(f"exec response error: {err}", exc_info=True)
            return

        try:
            self.exec_command(message_method, message)

        except Exception as err:
            LOGGER.error(err)

            # send error status for request message
            if message_id is not None:
                self.send_response(
                    message_id, error={"code": 9001, "message": str(err)}
                )

    def exec_response(self, message: RPCMessage):
        try:
            method = self.request_map.pop(message["id"])
        except KeyError as err:
            raise InvalidMessage(f"invalid response 'id': {err}")
        else:
            self.exec_command(method, message)

    def exec_command(self, method: str, params: RPCMessage):
        try:
            func = self.command_map[method]
        except KeyError as err:
            raise InvalidMessage(f"method not found {err}")

        # exec function
        func(params)

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
        message = RPCMessage.cancel_request(request_id)
        self.request_map.pop(request_id)
        self.transport.send_message(message)

    def send_response(
        self, request_id: int, result: Optional[Any] = None, error: Optional[Any] = None
    ):
        self.transport.send_message(RPCMessage.response(request_id, result, error))

    def send_notification(self, method: str, params: Any):
        self.transport.send_message(RPCMessage.notification(method, params))


class StandardIO(AbstractTransport):
    """standard io Transport implementation"""

    BUFFER_LENGTH = 4096

    def __init__(self, executable: str, arguments: List[str]):

        self.server_command = [executable]
        if arguments:
            self.server_command.extend(arguments)

        self.server_process: subprocess.Popen = None

        # set default queue
        self.message_queue = queue.Queue()

    def set_receiver(self, q: queue.Queue):
        """set message receiver"""
        self.message_queue = q

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

        bmessage = message.to_bytes()
        try:
            self.server_process.stdin.write(bmessage)
            self.server_process.stdin.flush()

        except OSError as err:
            raise ServerOffline("server has terminated") from err

    def _listen_stdout(self):
        """listen stdout task"""

        while True:
            buf = self.server_process.stdout.read(self.BUFFER_LENGTH)
            self.message_queue.put(buf)

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
