from .completion import complete, CompletionError
from .hover import (
    get_definition,
    get_documentation,
    build_documentation,
    DocumentationError,
)
from .document_formatting import format_code, FormattingError
