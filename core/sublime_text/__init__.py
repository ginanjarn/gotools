import sublime


def show_completions(view: sublime.View) -> None:
    """Opens (forced) the sublime autocomplete window"""

    view.run_command(
        "auto_complete",
        {
            "disable_auto_insert": True,
            "next_completion_if_showing": False,
            "auto_complete_commit_on_tab": True,
        },
    )


def hide_completions(view: sublime.View) -> None:
    """Opens (forced) the sublime autocomplete window"""
    view.run_command("hide_auto_complete")


def show_popup(
    view: sublime.View,
    content,
    flags=0,
    location=-1,
    max_width=1024,
    max_height=480,
    on_navigate=None,
    on_hide=None,
):
    if not flags:
        flags = sublime.HIDE_ON_MOUSE_MOVE_AWAY | sublime.COOPERATE_WITH_AUTO_COMPLETE

    view.show_popup(
        content, flags, location, max_width, max_height, on_navigate, on_hide
    )


class OutputPanel:
    """Output panel handler"""

    def __init__(self, window: sublime.Window, name: str):
        self.panel_name = name
        self.window = window

    def get_panel(self):
        panel = self.window.create_output_panel(self.panel_name)
        panel.set_read_only(False)
        return panel

    def append(self, *args: str):
        """append message to panel"""

        panel = self.get_panel()
        panel.run_command(
            "append", {"characters": "\n".join(args)},
        )

    def show(self):
        """show panel"""
        self.window.run_command("show_panel", {"panel": "output.%s" % self.panel_name})

    def destroy(self):
        """destroy panel"""
        self.window.destroy_output_panel(self.panel_name)


class DiagnosticPanel(OutputPanel):
    """Diagnostic output panel"""

    def __init__(self, window: sublime.Window):
        super().__init__(window, name="gotools-diagnostic")


class ErrorPanel(OutputPanel):
    """Error output panel"""

    def __init__(self, window: sublime.Window):
        super().__init__(window, name="gotools-error")
