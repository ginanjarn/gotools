"""view interface"""


import os
import sublime


def show_quickpane(document_view, items, callback):
    pass


def show_popup(
    document_view: "sublime.View",
    content: str,
    location: int,
    on_navigate: "Callable[[str], None]" = None,
) -> None:
    """Open popup"""

    document_view.show_popup(
        content,
        sublime.HIDE_ON_MOUSE_MOVE_AWAY | sublime.COOPERATE_WITH_AUTO_COMPLETE,
        location=location,
        max_width=1024,
        on_navigate=on_navigate,
    )


def show_completions(document_view: sublime.View) -> None:
    """show completion"""

    document_view.run_command("hide_auto_complete")
    document_view.run_command(
        "auto_complete",
        {
            "disable_auto_insert": True,
            "next_completion_if_showing": False,
            "auto_complete_commit_on_tab": True,
        },
    )


def open_link(view: sublime.View, link: "Dict[str, Any]") -> None:
    """open link"""

    if not link:
        return None

    view_path = view.file_name()
    path = "{mod_path}:{line}:{character}".format(
        mod_path=view_path if link["path"] is None else link["path"],
        line=0 if link["line"] is None else link["line"],
        character=0 if link["character"] is None else link["character"] + 1,
    )
    return view.window().open_file(path, sublime.ENCODED_POSITION)
