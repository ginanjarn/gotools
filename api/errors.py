"""rpc errors definition"""


class BaseRPCError(Exception):
    code = -1
    message = "error"


class ParseError(BaseRPCError):
    """message not comply to jsonrpc 2.0 specification"""

    code = -32700
    message = "parse error"


class InvalidRequest(BaseRPCError):
    """invalid request"""

    code = -32600
    message = "invalid request"


class MethodNotFound(BaseRPCError):
    """method not found"""

    code = -32601
    message = "method not found"


class InvalidParams(BaseRPCError):
    """invalid params"""

    code = -32602
    message = "invalid params"


class InternalError(BaseRPCError):
    """internal error"""

    code = -32603
    message = "internal error"


class ServerNotInitialized(BaseRPCError):
    """workspace not initialize"""

    code = -32002
    message = "server not initialized"


class InvalidResource(InternalError):
    """invalid resource"""

    message = "invalid resource"
