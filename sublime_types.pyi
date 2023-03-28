# Don't evaluate type annotations at runtime
from __future__ import annotations

from typing import Tuple, Union, List, Dict, Optional, Any  # type:ignore
from typing_extensions import TypeAlias
from sublime import KindId, CompletionItem  # type:ignore

DIP: TypeAlias = "float"
Vector: TypeAlias = "Tuple[DIP, DIP]"
Point: TypeAlias = "int"
Value: TypeAlias = "Union[bool, str, int, float, List[Any], Dict[str, Any]]"
CommandArgs: TypeAlias = "Optional[Dict[str, Value]]"
Kind: TypeAlias = "Tuple[KindId, str, str]"
Event: TypeAlias = "dict"
CompletionValue: TypeAlias = "Union[str, Tuple[str, str], CompletionItem]"
