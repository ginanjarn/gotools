import logging

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
stream_formatter = "%(levelname)s %(asctime)s: %(filename)s:%(lineno)s:  %(message)s"
sh.setFormatter(logging.Formatter(stream_formatter))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)

import sublime, sublime_plugin
import subprocess, os, threading
from collections import namedtuple


def get_completion(source: str, workdir: str, location: int):

    command = ["gocode", "-f=csv", "autocomplete", "c%s" % location]
    env = os.environ.copy()

    if os.name == "nt":
        # STARTUPINFO only available on windows
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
    else:
        startupinfo = None

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            shell=True,
            env=env,
            cwd=workdir,
        )
        sout, serr = process.communicate(source.encode("utf8"))
        if serr:
            logger.debug(
                "completion error:\n%s" % ("\n".join(serr.decode().splitlines()))
            )
            return None
        return sout.decode("utf8")

    except OSError as err:
        logger.error(err)


GocodeResult = namedtuple("GocodeResult", ["type_", "name", "data", "package"])


class Completion:
    """completion halder"""

    def __init__(self, completions):
        self.completions = completions

    def to_sublime(self):
        return (
            self.completions,
            sublime.INHIBIT_WORD_COMPLETIONS,
        )

    @staticmethod
    def transform_type(type_: str):
        type_map = {
            "func": sublime.KIND_FUNCTION,
            "package": sublime.KIND_NAMESPACE,
            "type": sublime.KIND_TYPE,
            "const": sublime.KIND_NAVIGATION,
            "var": sublime.KIND_VARIABLE,
            # "PANIC": sublime.KIND_NAVIGATION, # gocode error
        }
        return type_map.get(type_, sublime.KIND_AMBIGUOUS)

    @classmethod
    def from_gocode_csv(cls, raw: str):
        """parse from gocode csv"""

        logger.debug(raw)
        completions = []
        for line in raw.splitlines():
            raw = GocodeResult(*line.split(",,"))
            annotation = (
                "%s%s" % (raw.name, raw.data[4:]) if raw.type_ == "func" else raw.data
            )
            details = "<strong>%s%s</strong>" % (
                "" if raw.type_ == "package" else "%s." % raw.package,
                raw.name,
            )
            completions.append(
                sublime.CompletionItem(
                    trigger=raw.name,
                    annotation=annotation,
                    kind=cls.transform_type(raw.type_),
                    details=details,
                )
            )

        return cls(completions)


def valid_source(view: sublime.View, location: int = 0) -> bool:
    """valid go source code"""
    return view.match_selector(location, "source.go")


def valid_scope(view: sublime.View, location: int) -> bool:
    """valid scope for completion"""

    if view.match_selector(location, "source.go string"):
        return False

    return True


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


PLUGIN_ENABLED = False


def enable_plugin(enable=True):
    global PLUGIN_ENABLED

    PLUGIN_ENABLED = enable


class Event(sublime_plugin.ViewEventListener):
    """Event handler"""

    def __init__(self, view: sublime.View):
        self.view = view
        self.completions = None

    def completion_thread(self, view: sublime.View):
        source = view.substr(sublime.Region(0, view.size()))
        location = view.sel()[0].a
        workdir = os.path.dirname(view.file_name())

        raw_completions = get_completion(source, workdir, location)
        completion = Completion.from_gocode_csv(raw_completions)

        self.completions = completion.to_sublime()

        show_completions(view)

    def on_query_completions(self, prefix: str, locations):
        if not PLUGIN_ENABLED:
            return None

        if not valid_scope(self.view, locations[0]):
            return ([], sublime.INHIBIT_EXPLICIT_COMPLETIONS)

        if self.completions:
            completions = self.completions
            self.completions = None
            return completions

        thread = threading.Thread(target=self.completion_thread, args=(self.view,))
        thread.start()
        return None

    def on_activated(self):
        if valid_source(self.view):
            enable_plugin()
        else:
            enable_plugin(False)
