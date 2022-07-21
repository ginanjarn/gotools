"""File watcher"""

import logging
import os
import pathlib

from dataclasses import dataclass
from typing import Iterator, Iterable

LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)

TYPE_CREATED = 1
TYPE_CHANGED = 2
TYPE_DELETED = 3


@dataclass
class ChangeItem:
    """file change item"""

    file_name: str
    change_type: int


class Watcher:
    """Watcher file modification"""

    def __init__(self, root_folder: str = "", pattern: str = "*"):
        self.root_folder = pathlib.Path(root_folder)
        self.glob_pattern = pattern
        self.watched_files = {}

    def set_root_folder(self, path: str):
        self.root_folder = pathlib.Path(path)
        self.watched_files = {}

    def set_glob(self, pattern: str):
        self.glob_pattern = pattern
        self.watched_files = {}

    def _file_modified(self, path: pathlib.Path) -> bool:
        file_name = str(path)
        if path.stat().st_mtime > self.watched_files[file_name]["modified"]:
            return True
        return False

    def _add_watched_files(self, path: pathlib.Path) -> None:
        file_name = str(path)
        self.watched_files[file_name] = {"modified": path.stat().st_mtime}

    def _scan_removed(
        self, glob_resuls: Iterable[pathlib.Path]
    ) -> Iterator[ChangeItem]:

        scanned_files_str = [str(path) for path in glob_resuls]
        removed_files = set()

        for file_name in self.watched_files:
            if file_name not in scanned_files_str:
                removed_files.add(file_name)
                yield ChangeItem(file_name, TYPE_DELETED)

        for file_name in removed_files:
            del self.watched_files[file_name]

        LOGGER.debug("watched_files: %s", self.watched_files)

    def poll(self) -> Iterator[ChangeItem]:
        """poll file changes"""

        workspace = pathlib.Path(self.root_folder)
        glob_resuls = list(workspace.glob(self.glob_pattern))

        for path in glob_resuls:
            file_name = str(path)

            if file_name in self.watched_files:
                if self._file_modified(path):
                    LOGGER.debug("file modified: %s", file_name)
                    self.watched_files[file_name]["modified"] = path.stat().st_mtime
                    yield ChangeItem(file_name, TYPE_CHANGED)

            else:
                self._add_watched_files(path)
                LOGGER.debug("file added: %s", file_name)
                yield ChangeItem(file_name, TYPE_CREATED)

        LOGGER.debug("watched_files: %s", self.watched_files)
        yield from self._scan_removed(glob_resuls)
