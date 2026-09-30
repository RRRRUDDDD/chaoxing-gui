"""Filesystem checks shared by download paths."""

import stat
from pathlib import Path

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def reject_links(path) -> Path:
    """Refuse a path whose existing parts include a symlink, junction or other
    reparse point; missing parts are fine. Returns the absolute path.
    """
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
            raise ValueError("下载目录不能包含符号链接、目录联接或重解析点")
    return path
