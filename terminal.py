"""handle temninal related command"""

import threading
from pathlib import Path
from typing import List

import sublime
import sublime_plugin

from .api import terminal


def get_workspace_path(view: sublime.View) -> str:
    window = view.window()
    file_name = view.file_name()

    if folders := [
        folder for folder in window.folders() if file_name.startswith(folder)
    ]:
        return max(folders)
    return str(Path(file_name).parent)


def valid_context(view: sublime.View, point: int):
    return view.match_selector(point, "source.go")


class GotoolsGoCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit, arguments: List[str]):
        thread = threading.Thread(target=self._exec, args=(arguments,))
        thread.start()

    def _exec(self, arguments: List[str]):
        command = ["go"]
        command.extend(arguments)
        cwd = get_workspace_path(self.view)

        ret = terminal.exec_cmd_nobuffer(command, cwd=cwd)
        print(f"exec terminated with exit code {ret}")

    def is_visible(self):
        return valid_context(self.view, 0)


class GotoolsGoModInitCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit):
        def init_module(name: str):
            if not name:
                print("module name undefined")
                return

            self.view.run_command("gotools_go", {"arguments": ["mod", "init", name]})

        self.view.window().show_input_panel(
            caption="Module name",
            initial_text="example.com/hello",
            on_done=init_module,
            on_change=None,
            on_cancel=None,
        )

    def is_visible(self):
        return valid_context(self.view, 0)


class GotoolsInstallToolsCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit):
        thread = threading.Thread(target=self._install_tools)
        thread.start()

    def _install(self, package_name):
        print(f"installing {package_name}")

        command = ["go", "install", package_name]
        if ret := terminal.exec_cmd_nobuffer(command):
            print(f"install failed with exit code {ret}")
        else:
            print(f"{package_name} successfully installed")

    def _install_tools(self):
        tools = [
            "golang.org/x/tools/gopls@latest",
            "honnef.co/go/tools/cmd/staticcheck@latest",
        ]

        for tool in tools:
            self._install(tool)

    def is_visible(self):
        return valid_context(self.view, 0)
