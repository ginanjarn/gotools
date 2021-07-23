"""backend api"""


import logging
import os
import subprocess


logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
sh = logging.StreamHandler()
stream_formatter = "%(levelname)s %(asctime)s: %(filename)s:%(lineno)s:  %(message)s"
sh.setFormatter(logging.Formatter(stream_formatter))
sh.setLevel(logging.DEBUG)
logger.addHandler(sh)


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


def get_documentation(source: str, workdir: str, location: int):

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


def get_formatted_code(source: str):

    command = ["gofmt"]
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
            # cwd=workdir,
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


def get_diagnostic(path: str, workdir: str = ""):
    """get diagnostic for file or directory"""

    command = ["go", "vet", path]
    env = os.environ.copy()

    if os.name == "nt":
        # STARTUPINFO only available on windows
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
    else:
        startupinfo = None

    if not workdir:
        workdir = os.path.dirname(path) if os.path.isfile(path) else path

    try:
        process = subprocess.Popen(
            command,
            # stdin=subprocess.PIPE,
            # stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            shell=True,
            env=env,
            cwd=workdir,
        )

        sout, serr = process.communicate()
        return serr.decode("utf8")

    except OSError as err:
        logger.error(err)
