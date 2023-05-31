"""client server api"""

import json
import logging
import os
import re
import threading
import subprocess
from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from typing import Optional, Union

from . import errors

URI = str
_PathLikeStr = str

LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(levelname)s %(filename)s:%(lineno)d  %(message)s")
sh = logging.StreamHandler()
sh.setFormatter(fmt)
LOGGER.addHandler(sh)


def path_to_uri(path: _PathLikeStr) -> URI:
    """convert path to uri"""
    return Path(path).as_uri()


def uri_to_path(uri: URI) -> _PathLikeStr:
    """convert uri to path"""
    return url2pathname(unquote(urlparse(uri).path))


class BaseHandler:
    """Base handler"""

    @staticmethod
    def flatten_method(method: str) -> str:
        return f"handle_{method}".replace("/", "_").replace(".", "_").lower()

    def handle(self, method: str, params: dict):
        LOGGER.info("handle '%s'", method)

        try:
            func = getattr(self, self.flatten_method(method))
        except AttributeError as err:
            raise errors.MethodNotFound(f"method not found {method!r}") from err

        else:
            return func(params)


class RPCMessage(dict):
    """rpc message"""

    @classmethod
    def request(cls, id, method, params):
        return cls({"id": id, "method": method, "params": params})

    @classmethod
    def notification(cls, method, params):
        return cls({"method": method, "params": params})

    @classmethod
    def response(cls, id, result, error):
        if error:
            return cls({"id": id, "error": error})
        return cls(
            {
                "id": id,
                "result": result,
            }
        )

    def dumps(self, *, as_bytes: bool = False):
        """dump rpc message to json text"""
        dumped = json.dumps(self)
        if as_bytes:
            return dumped.encode()
        return dumped

    @classmethod
    def load(cls, data: Union[str, bytes]):
        """load rpc message from json text"""
        return cls(json.loads(data))

    @staticmethod
    def exception_to_message(exception: Exception) -> dict:
        return {"message": str(exception), "code": 1}


if os.name == "nt":
    # if on Windows, hide process window
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
else:
    STARTUPINFO = None


class ContentIncomplete(ValueError):
    """content incomplete"""


class ServerNotRunning(Exception):
    """server not running"""


class StreamIO(ABC):
    """stream io"""

    @abstractmethod
    def read(self) -> bytes:
        """read stream"""

    @abstractmethod
    def write(self, data: bytes):
        """write stream"""


class StandardIO(StreamIO):
    def __init__(self, *, reader: BytesIO, writer: BytesIO):
        self.reader = reader
        self.writer = writer

    @staticmethod
    def _get_content_length(header: bytes):
        for line in header.splitlines():
            if found := re.match(rb"Content-Length: (\d+)", line):
                return int(found.group(1))
        raise ValueError("unable get 'Content-Length'")

    def read(self):
        """read stream"""

        # read header
        temp_header = BytesIO()
        # process will blocked until line satisfied or end of file
        while line := self.reader.readline():
            if line == b"\r\n":
                break
            temp_header.write(line)
        else:
            raise EOFError("stdout closed")

        content_length = self._get_content_length(temp_header.getvalue())

        temp_content = BytesIO()
        while True:
            # process will blocked until expected size satisfied or end of file
            content = self.reader.read(content_length)
            if not content:
                raise EOFError("stdout closed")
            temp_content.write(content)

            # in case received content is incomplete
            if len(temp_content.getvalue()) < content_length:
                continue

            return temp_content.getvalue()

    @staticmethod
    def _wraps(data: bytes) -> bytes:
        """wraps data to stream format"""
        header = b"Content-Length: %d" % len(data)
        return b"%s\r\n\r\n%s" % (header, data)

    def write(self, data: bytes):
        """write stream"""
        self.writer.write(self._wraps(data))
        self.writer.flush()


class Transport:
    def __init__(self, handler: BaseHandler):
        self.handler = handler

        self._request_map = {}
        self._canceled_requests = set()
        self._temp_request_id = -1
        self._server_process: subprocess.Popen = None

        self._run_server_event = threading.Event()
        self._request_map_lock = threading.Lock()

        self._stream: StreamIO = None

    def is_running(self) -> bool:
        # if process running, process.poll() return None
        if self._server_process and self._server_process.poll() is None:
            return True
        return False

    def run_server(self):
        # process must not blocking main process
        run_thread = threading.Thread(target=self._run_server, daemon=True)
        listen_thread = threading.Thread(target=self._listen, daemon=True)
        listen_stderr_thread = threading.Thread(target=self._listen_stderr, daemon=True)

        run_thread.start()
        listen_thread.start()
        listen_stderr_thread.start()

    def _run_server(self):
        command = ["gopls"]

        if LOGGER.level == logging.DEBUG:
            command.append("-veryverbose")

        LOGGER.debug("exec command: %s", command)

        self._server_process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=None,
            cwd=None,
            bufsize=0,
            startupinfo=STARTUPINFO,
        )

        self._stream = StandardIO(
            reader=self._server_process.stdout, writer=self._server_process.stdin
        )

        self._run_server_event.set()

    def terminate_server(self):
        self._run_server_event.clear()
        self._stream = None
        if self.is_running():
            self._server_process.kill()

    def new_request_id(self) -> int:
        self._temp_request_id += 1
        return self._temp_request_id

    def send_message(self, message: RPCMessage):
        # wait until server ready
        self._run_server_event.wait()

        message["jsonrpc"] = "2.0"
        content = message.dumps(as_bytes=True)

        self._stream.write(content)

    def _listen_stderr(self):
        # wait until server ready
        self._run_server_event.wait()

        while line := self._server_process.stderr.readline():
            print(f"[gopls]{line.strip().decode()}")

        # enforce server terminated
        self.terminate_server()

    def _listen(self):
        # wait until server ready
        self._run_server_event.wait()

        while True:

            try:
                content = self._stream.read()
                message = RPCMessage.load(content)
                self.handle_message(message)

            except Exception as err:
                LOGGER.debug(content)
                LOGGER.error(err, exc_info=True)
                self.terminate_server()
                return

    def handle_message(self, message: RPCMessage):
        id = message.get("id")

        # handle server command
        method = message.get("method")
        if method:
            if id is None:
                self.handle_notification(message)
            else:
                self.handle_request(message)

        # handle server response
        elif id is not None:
            self.handle_response(message)

        else:
            LOGGER.error("invalid message: %s", message)

    def handle_request(self, message: RPCMessage):
        result = None
        error = None
        try:
            result = self.handler.handle(message["method"], message["params"])
        except Exception as err:
            LOGGER.debug(err, exc_info=True)
            error = RPCMessage.exception_to_message(err)

        self.send_response(message["id"], result, error)

    def handle_notification(self, message: RPCMessage):
        try:
            self.handler.handle(message["method"], message["params"])
        except Exception as err:
            LOGGER.debug(err, exc_info=True)

    def handle_response(self, message: RPCMessage):
        with self._request_map_lock:
            method = self._request_map.pop(message["id"], "unknown")

            # check if request canceled
            if message["id"] in self._canceled_requests:
                self._canceled_requests.remove(message["id"])
                return

            try:
                self.handler.handle(method, message)
            except Exception as err:
                LOGGER.debug(err, exc_info=True)

    def send_request(self, method: str, params: dict):
        with self._request_map_lock:
            # cancel previous request
            for req_id, meth in self._request_map.items():
                if meth == method:
                    self._canceled_requests.add(req_id)

            req_id = self.new_request_id()
            self.send_message(RPCMessage.request(req_id, method, params))
            self._request_map[req_id] = method

    def send_notification(self, method: str, params: dict):
        self.send_message(RPCMessage.notification(method, params))

    def send_response(
        self, id: int, result: Optional[dict] = None, error: Optional[dict] = None
    ):
        self.send_message(RPCMessage.response(id, result, error))
