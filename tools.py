import logging

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
stream_formatter = "%(levelname)s %(asctime)s: %(filename)s:%(lineno)s:  %(message)s"
sh.setFormatter(logging.Formatter(stream_formatter))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)

from functools import wraps
import os
import subprocess
import threading

import sublime
import sublime_plugin

PROCESS_LOCK = threading.Lock()


def process_lock(func):
    """process pipeline. single process allowed"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if PROCESS_LOCK.locked():
            return None

        status_key = "gotools"
        value = "Installing tools"
        view = sublime.active_window().active_view()
        view.set_status(status_key, value)
        with PROCESS_LOCK:
            function = func(*args, **kwargs)
            view.erase_status(status_key)
            return function

    return wrapper


TOOLS_SET = {}


class GotoolsUpdateToolsCommand(sublime_plugin.TextCommand):
    """document update tools command"""

    def run(self, edit):
        file_dir = os.path.dirname(__file__)
        requirement_path = os.path.join(file_dir, "requirements")
        with open(requirement_path, "r") as file:
            content = file.read()
            logger.debug(content)
            self.tools = content.splitlines()
            self.tools.insert(0, "All")
            logger.debug(self.tools)

            self.select_tool(self.view.window())

    def select_tool(self, window: sublime.Window):
        window.show_quick_panel(
            items=self.tools,
            flags=sublime.KEEP_OPEN_ON_FOCUS_LOST,
            selected_index=0,
            on_select=self.on_select_tool,
        )

    def on_select_tool(self, index=-1):
        if index < 0:
            return

        global TOOLS_SET

        if index == 0:
            TOOLS_SET = set(self.tools[1:])
        else:
            TOOLS_SET = set((self.tools[index],))

        thread = threading.Thread(target=self.do_install)
        thread.start()

    @process_lock
    def do_install(self):

        for tool in TOOLS_SET:
            try:
                print("installing: %s" % tool)
                self.install_tool(tool)

            except Exception as err:
                print("error installing %s:\n%s\n" % (tool, err))

    def install_tool(self, tool_name: str):

        # if no version defined
        if len(tool_name.split()) == 1:
            tool_name = "%s@latest" % tool_name

        command = ["go", "install", tool_name]
        logger.debug("command: %s", command)
        env = os.environ

        if os.name == "nt":
            # STARTUPINFO only available on windows
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
        else:
            startupinfo = None

        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            shell=True,
            env=env,
            # cwd=workdir,
        )
        _, serr = process.communicate()
        if serr:
            err_message = "\n".join(serr.decode().splitlines())
            raise Exception(err_message)
