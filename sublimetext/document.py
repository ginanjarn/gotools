"""documen interface"""


import difflib
import sublime


def diff_sanity_check(source, changes):
	"""check if changes in same content"""

    if source != changes:
        raise ValueError("unmatched changes")


def apply_changes(view, edit, changes):
	"""apply document changes"""

    source = view.substr(sublime.Region(0, view.size()))

    diff = difflib.ndiff(source.splitlines(), changes.splitlines())
    i = 0
    for line in diff:
        if line.startswith("?"):  # skip hint lines
            continue

        l = (len(line) - 2) + 1
        if line.startswith("-"):
            diff_sanity_check(view.substr(sublime.Region(i, i + l - 1)), line[2:])
            view.erase(edit, sublime.Region(i, i + l))
        elif line.startswith("+"):
            view.insert(edit, i, line[2:] + "\n")
            i += l
        else:
            diff_sanity_check(view.substr(sublime.Region(i, i + l - 1)), line[2:])
            i += l
