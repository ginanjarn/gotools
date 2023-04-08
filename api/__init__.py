"""client server api"""

import json
import logging
import os
import re
import threading
import subprocess
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


class StreamBuffer:
    def __init__(self, buffer: bytes = b""):
        self._buffer = [buffer] if buffer else []

    def __repr__(self):
        return f"StreamBuffer(buffer={self.buffer!r})"

    @property
    def buffer(self) -> bytes:
        return b"".join(self._buffer)

    def put(self, b: bytes, /):
        self._buffer.append(b)

    @staticmethod
    def _get_content_len(header: bytes) -> int:
        pattern = re.compile(rb"Content-Length: (\d+)")
        if found := pattern.search(header):
            return int(found.group(1))

        raise ValueError("unable find Content-Length from header")

    def get(self) -> bytes:
        buffer = self.buffer
        if not buffer:
            raise EOFError("buffer empty")

        sep = b"\r\n\r\n"
        header = b""

        if (index := buffer.find(sep)) and index > -1:
            header = buffer[:index]
        else:
            LOGGER.debug("buffer: %s", buffer)
            raise ValueError("unable get message header")

        # get content length from header
        defined_len = self._get_content_len(header)

        start = len(header) + len(sep)
        end = start + defined_len

        content = buffer[start:end]
        # compare received content size
        expected_len = len(content)
        if expected_len < defined_len:
            raise ContentIncomplete(f"want {defined_len}, expected {expected_len}")

        # restore unread bytes
        self._buffer = [buffer[end:]]

        return content

    @staticmethod
    def wraps(data: bytes) -> bytes:
        """wraps data to stream format"""
        header = b"Content-Length: %d" % len(data)
        return b"%s\r\n\r\n%s" % (header, data)


class Transport:
    def __init__(self, handler: BaseHandler):
        self.handler = handler

        self._request_map = {}
        self._canceled_requests = set()
        self._temp_request_id = -1
        self._server_process: subprocess.Popen = None

        self._run_server_event = threading.Event()
        self._request_map_lock = threading.Lock()

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
        command = ["clangd"]

        if LOGGER.level == logging.DEBUG:
            command.append("--log=verbose")

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
        self._run_server_event.set()

    def terminate_server(self):
        self._run_server_event.clear()
        if self.is_running():
            self._server_process.kill()

    def new_request_id(self) -> int:
        self._temp_request_id += 1
        return self._temp_request_id

    def send_message(self, message: RPCMessage):
        message["jsonrpc"] = "2.0"
        write_data = StreamBuffer.wraps(message.dumps(as_bytes=True))

        try:
            self._server_process.stdin.write(write_data)
            self._server_process.stdin.flush()

        except Exception as err:
            if not self.is_running():
                raise ServerNotRunning("server not running") from err
            raise err

    def _listen_stderr(self):
        # wait until server ready
        self._run_server_event.wait()

        while line := self._server_process.stderr.readline():
            print(f"..{line.strip().decode()}")

    def _listen(self):
        # wait until server ready
        self._run_server_event.wait()

        stream = StreamBuffer()
        while True:
            if chunk := self._server_process.stdout.read(1024):
                stream.put(chunk)
            else:
                break

            while True:
                try:
                    content = stream.get()
                except (ContentIncomplete, EOFError):
                    break

                try:
                    message = RPCMessage.load(content)
                    self.handle_message(message)

                except Exception as err:
                    LOGGER.error(err, exc_info=True)
                    self.terminate_server()

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
