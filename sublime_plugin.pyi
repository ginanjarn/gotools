# Don't evaluate type annotations at runtime
from __future__ import annotations

import importlib
import threading
from typing import Any, List, Optional, Union, Tuple

import sublime
from sublime import (
    View,
    Window,
    HoverZone,
    QueryOperator,
    AutoCompleteFlags,
    CompletionList,
    TextChange,
)
from sublime_types import Point, Value, CompletionValue, CommandArgs


api_ready: bool = ...

deferred_plugin_loadeds: list = ...

application_command_classes: list = ...
window_command_classes: list = ...
text_command_classes: list = ...

view_event_listener_classes: list = ...
view_event_listeners: dict = ...

all_command_classes = [
    application_command_classes,
    window_command_classes,
    text_command_classes,
]

all_callbacks: list = ...

pending_on_activated_async_lock: threading.Lock = ...

pending_on_activated_async_callbacks: dict = ...

view_event_listener_excluded_callbacks: dict = ...

text_change_listener_classes: list = ...
text_change_listener_callbacks: dict = ...
text_change_listeners: dict = ...

profile: dict = ...


def add_profiling(event_handler):
    """
    Decorator to measure blocking event handler methods. Also prevents
    exceptions from interrupting other events handlers.

    :param event_handler:
        The event handler method - must be an unbound method

    :return:
        The decorated method

    :meta private:
    """


def trap_exceptions(event_handler):
    """
    Decorator to prevent exceptions from interrupting other events handlers.

    :param event_handler:
        The event handler method - must be an unbound method

    :return:
        The decorated method

    :meta private:
    """


def decorate_handler(cls, method_name):
    """
    Decorates an event handler method with exception trapping, and in the case
    of blocking calls, profiling.

    :param cls:
        The class object to decorate
    :param method_name:
        A unicode string of the name of the method to decorate

    :meta private:
    """


def unload_module(module):
    ...


def unload_plugin(modulename):
    ...


def reload_plugin(modulename):
    ...


def load_module(m):
    ...


def synthesize_on_activated_async():
    ...


def notify_application_commands():
    ...


def create_application_commands():
    ...


def create_window_commands(window_id):
    ...


def create_text_commands(view_id):
    ...


def on_api_ready():
    ...


def is_view_event_listener_applicable(cls, view):
    ...


def create_view_event_listeners(classes, view):
    ...


def check_view_event_listeners(view):
    ...


def attach_view(view):
    ...


check_all_view_event_listeners_scheduled: bool = ...


def check_all_view_event_listeners():
    ...


def detach_view(view):
    ...


def find_view_event_listener(view, cls):
    ...


def attach_buffer(buf):
    ...


def check_text_change_listeners(buf):
    ...


def detach_buffer(buf):
    ...


def plugin_module_for_obj(obj):
    # Since objects in plugins may be defined deep in a sub-module, if we want
    # to filter by a module, we must make sure we are only looking at the
    # first two module labels
    ...


def el_callbacks(name, listener_only=False):
    ...


def vel_callbacks(v, name, listener_only=False):
    ...


def run_view_callbacks(name, view_id, *args, el_only=False):
    ...


def run_window_callbacks(name, window_id, *args):
    ...


def on_init(module):
    """
    Trigger the on_init() methods on EventListener and ViewEventListener
    objects. This is method that allows event listeners to run something
    once per view, even if the view is done loading before the listener
    starts listening.

    :param module:
        A unicode string of the name of a plugin module to filter listeners by

    :meta private:
    """


def on_new(view_id):
    ...


def on_new_async(view_id):
    ...


def on_new_buffer(buffer_id):
    ...


def on_new_buffer_async(buffer_id):
    ...


def on_associate_buffer(buffer_id):
    ...


def on_associate_buffer_async(buffer_id):
    ...


def on_close_buffer(buffer_id):
    ...


def on_close_buffer_async(buffer_id):
    ...


def on_clone(view_id):
    ...


def on_clone_async(view_id):
    ...


class Summary:
    def __init__(self):
        ...

    def record(self, x):
        ...


def get_profiling_data():
    ...


def on_load(view_id):
    ...


def on_load_async(view_id):
    ...


def on_revert(view_id):
    ...


def on_revert_async(view_id):
    ...


def on_reload(view_id):
    ...


def on_reload_async(view_id):
    ...


def on_pre_close(view_id):
    ...


def on_close(view_id):
    ...


def on_pre_save(view_id):
    ...


def on_pre_save_async(view_id):
    ...


def on_post_save(view_id):
    ...


def on_post_save_async(view_id):
    ...


def on_pre_move(view_id):
    ...


def on_post_move(view_id):
    ...


def on_post_move_async(view_id):
    ...


def on_modified(view_id):
    ...


def on_modified_async(view_id):
    ...


def on_selection_modified(view_id):
    ...


def on_selection_modified_async(view_id):
    ...


def on_activated(view_id):
    ...


def on_activated_async(view_id):
    ...


def on_deactivated(view_id):
    ...


def on_deactivated_async(view_id):
    ...


def on_query_context(view_id, key, operator, operand, match_all):
    ...


def normalise_completion(c):
    ...


class MultiCompletionList:
    def __init__(self, num_completion_lists, view_id, req_id):
        ...

    def completions_ready(self, completions, flags):
        ...


def on_query_completions(view_id, req_id, prefix, locations):
    ...


def on_hover(view_id, point, hover_zone):
    ...


def on_text_command(view_id, name, args):
    ...


def on_window_command(window_id, name, args):
    ...


def on_post_text_command(view_id, name, args):
    ...


def on_post_window_command(window_id, name, args):
    ...


def on_new_project(window_id):
    ...


def on_new_project_async(window_id):
    ...


def on_load_project(window_id):
    ...


def on_load_project_async(window_id):
    ...


def on_pre_save_project(window_id):
    ...


def on_post_save_project(window_id):
    ...


def on_post_save_project_async(window_id):
    ...


def on_pre_close_project(window_id):
    ...


def on_new_window(window_id):
    ...


def on_new_window_async(window_id):
    ...


def on_pre_close_window(window_id):
    ...


def on_exit(log_path):
    """on_exit() is called once the API it shutdown, which means that stdout
    will not be visible for debugging. Thus we write to a log file.
    """


class CommandInputHandler:
    """ """

    def name(self) -> str:
        """
        The command argument name this input handler is editing. Defaults to
        ``foo_bar`` for an input handler named ``FooBarInputHandler``.
        """

    def placeholder(self) -> str:
        """
        Placeholder text is shown in the text entry box before the user has
        entered anything. Empty by default.
        """

    def initial_text(self) -> str:
        """
        Initial text shown in the text entry box. Empty by default.
        """

    def initial_selection(self) -> list[tuple[int, int]]:
        """
        A list of 2-element tuples, defining the initially selected parts of the
        initial text.

        .. since:: 4081
        """

    def preview(self, text: str) -> str | sublime.Html:
        """
        Called whenever the user changes the text in the entry box. The returned
        value (either plain text or HTML) will be shown in the preview area of
        the *Command Palette*.
        """

    def validate(self, text: str) -> bool:
        """
        Called whenever the user presses enter in the text entry box.
        Return :py:`False` to disallow the current value.
        """

    def cancel(self):
        """
        Called when the input handler is canceled, either by the user pressing
        backspace or escape.
        """

    def confirm(self, text: str):
        """
        Called when the input is accepted, after the user has pressed enter and
        the text has been validated.
        """

    def next_input(self, args) -> Optional[CommandInputHandler]:
        """
        Return the next input after the user has completed this one. May return
        :py:`None` to indicate no more input is required, or
        `sublime_plugin.BackInputHandler()` to indicate that the input handler
        should be popped off the stack instead.
        """

    def want_event(self) -> bool:
        """
        Whether the `validate()` and `confirm()` methods should received a
        second `Event` parameter. Returns :py:`False` by default.

        .. since:: 4096
        """


class BackInputHandler(CommandInputHandler):
    """ """

    def name(self):
        ...


class TextInputHandler(CommandInputHandler):
    """
    TextInputHandlers can be used to accept textual input in the *Command
    Palette*. Return a subclass of this from `Command.input()`.

    *For an input handler to be shown to the user, the command returning the
    input handler MUST be made available in the Command Palette by adding the
    command to a :path:`Default.sublime-commands` file.*
    """

    def description(self, text: str) -> str:
        """
        The text to show in the *Command Palette* when this input handler is not
        at the top of the input handler stack. Defaults to the text the user
        entered.
        """


class ListInputHandler(CommandInputHandler):
    """
    ListInputHandlers can be used to accept a choice input from a list items in
    the *Command Palette*. Return a subclass of this from `Command.input()`.

    *For an input handler to be shown to the user, the command returning the
    input handler MUST be made available in the Command Palette by adding the
    command to a :path:`Default.sublime-commands` file.*
    """

    def list_items(
        self,
    ) -> list[str] | tuple[list[str], int] | list[tuple[str, Value]] | tuple[
        list[tuple[str, Value]], int
    ] | list[sublime.ListInputItem] | tuple[list[sublime.ListInputItem], int]:
        """
        This method should return the items to show in the list.

        The returned value may be a ``list`` of item, or a 2-element ``tuple``
        containing a list of items, and an ``int`` index of the item to
        pre-select.

        The each item in the list may be one of:

        * A string used for both the row text and the value passed to the
          command
        * A 2-element tuple containing a string for the row text, and a `Value`
          to pass to the command
        * .. since:: 4095
            A `sublime.ListInputItem` object
        """

    def description(self, value, text: str) -> str:
        """
        The text to show in the *Command Palette* when this input handler is not
        at the top of the input handler stack. Defaults to the text of the list
        item the user selected.
        """


class Command:
    """ """

    def name(self) -> str:
        """
        Return the name of the command. By default this is derived from the name
        of the class.
        """

    def is_enabled(self) -> bool:
        """
        Return whether the command is able to be run at this time. Command
        arguments are passed as keyword arguments. The default implementation
        simply always returns :py:`True`.
        """

    def is_visible(self) -> bool:
        """
        Return whether the command should be shown in the menu at this time.
        Command arguments are passed as keyword arguments. The default
        implementation always returns :py:`True`.
        """

    def is_checked(self) -> bool:
        """
        Return whether a checkbox should be shown next to the menu item. Command
        arguments are passed as keyword arguments. The :path:`.sublime-menu`
        file must have the ``"checkbox"`` key set to :json:`true` for this to
        be used.
        """

    def description(self) -> Optional[str]:
        """
        Return a description of the command with the given arguments. Command
        arguments are passed as keyword arguments. Used in the menu, if no
        caption is provided. Return :py:`None` to get the default description.
        """

    def filter_args(self, args):
        ...

    def want_event(self) -> bool:
        """
        Return whether to receive an `Event` argument when the command is
        triggered by a mouse action. The event information allows commands to
        determine which portion of the view was clicked on. The default
        implementation returns :py:`False`.
        """

    def input(self, args: dict) -> Optional[CommandInputHandler]:
        """
        If this returns something other than :py:`None`, the user will be
        prompted for an input before the command is run in the *Command
        Palette*.

        .. since:: 3154
        """

    def input_description(self) -> str:
        """
        Allows a custom name to be show to the left of the cursor in the input
        box, instead of the default one generated from the command name.

        .. since:: 3154
        """

    def run(self):
        """
        Called when the command is run. Command arguments are passed as keyword
        arguments.
        """


class ApplicationCommand(Command):
    """
    A `Command` instantiated just once.
    """

    def run(self):
        """:meta private:"""


class WindowCommand(Command):
    """
    A `Command` instantiated once per window. The `Window` object may be
    retrieved via `self.window <window>`.
    """

    window: sublime.Window

    def __init__(self, window: sublime.Window):
        ...

    def run(self):
        """:meta private:"""


class TextCommand(Command):
    """
    A `Command` instantiated once per `View`. The `View` object may be retrieved
    via `self.view <view>`.
    """

    view: sublime.View

    def __init__(self, view: sublime.View):
        ...

    def run(self, edit: sublime.Edit):
        """
        Called when the command is run. Command arguments are passed as keyword
        arguments.
        """


class EventListener:
    def on_init(self, views: List[View]):

        """Called once with a list of views that were loaded before the EventListener
        was instantiated

        .. since:: 4050
        """

    def on_exit(self):

        """Called once after the API has shut down, immediately before the
        plugin_host process exits

        .. since:: 4050
        """

    def on_new(self, view: View):

        """Called when a new file is created."""

    def on_new_async(self, view: View):

        """Called when a new buffer is created. Runs in a separate thread, and does
        not block the application.
        """

    def on_associate_buffer(self, buffer: View):

        """Called when a buffer is associated with a file. buffer will be a Buffer object.

        .. since:: 4084
        """

    def on_associate_buffer_async(self, buffer: View):

        """Called when a buffer is associated with file. Runs in a separate thread,
        and does not block the application. buffer will be a Buffer object.

        .. since:: 4084
        """

    def on_clone(self, view: View):

        """Called when a view is cloned from an existing one."""

    def on_clone_async(self, view: View):

        """Called when a view is cloned from an existing one. Runs in a separate
        thread, and does not block the application.
        """

    def on_load(self, view: View):

        """Called when the file is finished loading."""

    def on_load_async(self, view: View):

        """Called when the file is finished loading. Runs in a separate thread, and
        does not block the application."""

    def on_reload(self, view: View):

        """Called when the View is reloaded.

        .. since:: 4050
        """

    def on_reload_async(self, view: View):

        """Called when the View is reloaded. Runs in a separate thread, and does
        not block the application.

        .. since:: 4050
        """

    def on_revert(self, view: View):

        """Called when the View is reverted.

        .. since:: 4050
        """

    def on_revert_async(self, view: View):

        """Called when the View is reverted. Runs in a separate thread, and does
        not block the application.

        .. since:: 4050
        """

    def on_pre_move(self, view: View):

        """Called right before a view is moved between two windows, passed the View
        object.

        .. since:: 4050
        """

    def on_post_move(self, view: View):

        """Called right after a view is moved between two windows, passed the View
        object.

        .. since:: 4050
        """

    def on_post_move_async(self, view: View):

        """Called right after a view is moved between two windows, passed the View
        object. Runs in a separate thread, and does not block the application.

        .. since:: 4050
        """

    def on_pre_close(self, view: View):

        """Called when a view is about to be closed. The view will still be in the
        window at this point.
        """

    def on_close(self, view: View):

        """Called when a view is closed (note, there may still be other views into
        the same buffer).
        """

    def on_pre_save(self, view: View):

        """Called just before a view is saved."""

    def on_pre_save_async(self, view: View):

        """Called just before a view is saved. Runs in a separate thread, and does
        not block the application."""

    def on_post_save(self, view: View):

        """Called after a view has been saved."""

    def on_post_save_async(self, view: View):

        """Called after a view has been saved. Runs in a separate thread, and does
        not block the application.
        """

    def on_modified(self, view: View):

        """Called after changes have been made to a view."""

    def on_modified_async(self, view: View):

        """Called after changes have been made to a view. Runs in a separate
        thread, and does not block the application.
        """

    def on_selection_modified(self, view: View):

        """Called after the selection has been modified in a view."""

    def on_selection_modified_async(self, view: View):

        """Called after the selection has been modified in a view. Runs in a
        separate thread, and does not block the application.
        """

    def on_activated(self, view: View):

        """Called when a view gains input focus."""

    def on_activated_async(self, view: View):

        """Called when a view gains input focus. Runs in a separate thread, and
        does not block the application.
        """

    def on_deactivated(self, view: View):

        """Called when a view loses input focus."""

    def on_deactivated_async(self, view: View):

        """Called when a view loses input focus. Runs in a separate thread, and
        does not block the application.
        """

    def on_hover(self, view: View, point: Point, hover_zone: HoverZone):

        """Called when the user's mouse hovers over the view for a short period.

        :param view: The view
        :param point:
            The closest point in the view to the mouse location. The mouse may
            not actually be located adjacent based on the value of
            ``hover_zone``.
        :param hover_zone:
            Which element in Sublime Text the mouse has hovered over.
        """

    def on_query_context(
        self,
        view: View,
        key: str,
        operator: QueryOperator,
        operand: str,
        match_all: bool,
    ) -> Optional[bool]:

        """Called when determining to trigger a key binding with the given context
        key. If the plugin knows how to respond to the context, it should
        return either True of False. If the context is unknown, it should
        return None.

        :param key:
            The context key to query. This generally refers to specific state
            held by a plugin.
        :param operator:
            The operator to check against the operand; whether to check
            equality, inequality, etc.
        :param operand: The value against which to check using the ``operator``.
        :param match_all:
            This should be used if the context relates to the selections: does
            every selection have to match(``match_all == True``), or is at
            least one matching enough (``match_all == False``)?
        :returns:
            ``True`` or ``False`` if the plugin handles this context key and it
            either does or doesn't match. If the context is unknown return
            ``None``.
        """

    def on_query_completions(
        self, view: View, prefix: str, locations: List[Point]
    ) -> Union[
        None,
        List[CompletionValue],
        Tuple[List[CompletionValue], AutoCompleteFlags],
        CompletionList,
    ]:

        """Called whenever completions are to be presented to the user.

        :param prefix: The text already typed by the user.
        :param locations: The list of points being completed. Since this method
                          is called for all completions no matter the syntax,
                          ``self.view.match_selector(point, relevant_scope)``
                          should be called to determine if the point is
                          relevant.
        :returns: A list of completions in one of the valid formats or ``None``
                  if no completions are provided.
        """

    def on_text_command(
        self, view: View, command_name: str, args: CommandArgs
    ) -> (str, CommandArgs):

        """Called when a text command is issued.

        The listener may return a (command, arguments) tuple to rewrite the command,
        or None to run the command unmodified.
        """

    def on_window_command(
        self, window: Window, command_name: str, args: CommandArgs
    ) -> (str, CommandArgs):

        """Called when a window command is issued.
        The listener may return a (command, arguments) tuple to rewrite the command,
        or None to run the command unmodified.
        """

    def on_post_text_command(self, view: View, command_name: str, args: CommandArgs):
        """Called after a text command has been executed."""

    def on_post_window_command(
        self, window: Window, command_name: str, args: CommandArgs
    ):
        """Called after a window command has been executed."""

    def on_new_window(self, window: Window):

        """Called when a window is created, passed the Window object.

        .. since:: 4050
        """

    def on_new_window_async(self, window: Window):

        """Called when a window is created, passed the Window object. Runs in a separate thread,
        and does not block the application.

        .. since:: 4050
        """

    def on_pre_close_window(self, window: Window):

        """Called right before a window is closed, passed the Window object.

        .. since:: 4050
        """

    def on_new_project(self, window: Window):

        """Called right after a new project is created, passed the Window object.

        .. since:: 4050
        """

    def on_new_project_async(self, window: Window):

        """Called right after a new project is created, passed the Window object.
        Runs in a separate thread, and does not block the application.

        .. since:: 4050
        """

    def on_load_project(self, window: Window):

        """Called right after a project is loaded, passed the Window object.

        .. since:: 4050
        """

    def on_load_project_async(self, window: Window):

        """Called right after a project is loaded, passed the Window object.
        Runs in a separate thread, and does not block the application.

        .. since:: 4050
        """

    def on_pre_save_project(self, window: Window):

        """Called right before a project is saved, passed the Window object.

        .. since:: 4050
        """

    def on_post_save_project(self, window: Window):

        """Called right after a project is saved, passed the Window object.

        .. since:: 4050
        """

    def on_post_save_project_async(self, window: Window):

        """Called right after a project is saved, passed the Window object.
        Runs in a separate thread, and does not block the application.

        .. since:: 4050
        """

    def on_pre_close_project(self, window: Window):

        """Called right before a project is closed, passed the Window object."""


class ViewEventListener:
    """
    A class that provides similar event handling to `EventListener`, but bound
    to a specific view. Provides class method-based filtering to control what
    views objects are created for.
    """

    def on_load(self):

        """Called when the file is finished loading.

        .. since:: 3155
        """

    def on_load_async(self):

        """Same as `on_load` but runs in a separate thread, not blocking the
        application.

        .. since:: 3155
        """

    def on_reload(self):

        """Called when the file is reloaded.

        .. since:: 4050
        """

    def on_reload_async(self):

        """Same as `on_reload` but runs in a separate thread, not blocking the
        application.

        .. since:: 4050
        """

    def on_revert(self):

        """Called when the file is reverted.

        .. since:: 4050
        """

    def on_revert_async(self):

        """Same as `on_revert` but runs in a separate thread, not blocking the
        application.

        .. since:: 4050
        """

    def on_pre_move(self):

        """Called right before a view is moved between two windows.

        .. since:: 4050
        """

    def on_post_move(self):

        """Called right after a view is moved between two windows.

        .. since:: 4050
        """

    def on_post_move_async(self):

        """Same as `on_post_move` but runs in a separate thread, not blocking the
        application.

        .. since:: 4050
        """

    def on_pre_close(self):

        """Called when a view is about to be closed. The view will still be in the
        window at this point.

        .. since:: 3155
        """

    def on_close(self):

        """Called when a view is closed (note, there may still be other views into
        the same buffer).

        .. since:: 3155
        """

    def on_pre_save(self):

        """Called just before a view is saved.

        .. since:: 3155
        """

    def on_pre_save_async(self):

        """Same as `on_pre_save` but runs in a separate thread, not blocking the
        application.

        .. since:: 3155
        """

    def on_post_save(self):

        """Called after a view has been saved.

        .. since:: 3155
        """

    def on_post_save_async(self):

        """Same as `on_post_save` but runs in a separate thread, not blocking the
        application.

        .. since:: 3155
        """

    def on_modified(self):

        """Called after changes have been made to the view."""

    def on_modified_async(self):

        """Same as `on_modified` but runs in a separate thread, not blocking the
        application.
        """

    def on_selection_modified(self):

        """Called after the selection has been modified in the view."""

    def on_selection_modified_async(self):

        """Called after the selection has been modified in the view. Runs in a separate thread,
        and does not block the application."""

    def on_activated(self):

        """Called when a view gains input focus."""

    def on_activated_async(self):

        """Called when the view gains input focus. Runs in a separate thread, and does not
        block the application."""

    def on_deactivated(self):

        """Called when the view loses input focus."""

    def on_deactivated_async(self):

        """Called when the view loses input focus. Runs in a separate thread,
        and does not block the application."""

    def on_hover(self, point: Point, hover_zone: HoverZone):

        """Called when the user's mouse hovers over the view for a short period.

        :param point:
            The closest point in the view to the mouse location. The mouse may
            not actually be located adjacent based on the value of
            ``hover_zone``.
        :param hover_zone:
            Which element in Sublime Text the mouse has hovered over.
        """

    def on_query_context(
        self, key: str, operator: QueryOperator, operand: str, match_all: bool
    ) -> Optional[bool]:

        """Called when determining to trigger a key binding with the given context
        key. If the plugin knows how to respond to the context, it should
        return either True of False. If the context is unknown, it should
        return None.

        :param key: The context key to query. This generally refers to specific
                    state held by a plugin.
        :param operator: The operator to check against the operand; whether to
                         check equality, inequality, etc.
        :param operand: The value against which to check using the ``operator``.
        :param match_all: This should be used if the context relates to the
                          selections: does every selection have to match
                          (``match_all == True``), or is at least one matching
                          enough (``match_all == False``)?
        :returns: ``True`` or ``False`` if the plugin handles this context key
                  and it either does or doesn't match. If the context is unknown
                  return ``None``.
        """

    def on_query_completions(
        self, prefix: str, locations: List[Point]
    ) -> Union[
        None,
        List[CompletionValue],
        Tuple[List[CompletionValue], AutoCompleteFlags],
        CompletionList,
    ]:

        """Called whenever completions are to be presented to the user.

        :param prefix: The text already typed by the user.
        :param locations: The list of points being completed. Since this method
                          is called for all completions no matter the syntax,
                          ``self.view.match_selector(point, relevant_scope)``
                          should be called to determine if the point is
                          relevant.
        :returns: A list of completions in one of the valid formats or ``None``
                  if no completions are provided.
        """

    def on_text_command(
        self, command_name: str, args: CommandArgs
    ) -> Tuple[str, CommandArgs]:

        """Called when a text command is issued.

        The listener may return a ``(command, arguments)`` tuple to rewrite the
        command, or ``None`` to run the command unmodified.

        .. since:: 3155
        """

    def on_post_text_command(self, command_name: str, args: CommandArgs):

        """Called after a text command has been executed."""

    @classmethod
    def is_applicable(cls, settings: sublime.Settings) -> bool:
        """
        :returns: Whether this listener should apply to a view with the given `Settings`.
        """

    @classmethod
    def applies_to_primary_view_only(cls) -> bool:
        """
        :returns: Whether this listener should apply only to the primary view
                  for a file or all of its clones as well.
        """

    view: sublime.View

    def __init__(self, view: sublime.View):
        ...


class TextChangeListener:
    """
    A class that provides event handling about text changes made to a specific Buffer.
    Is separate from `ViewEventListener` since multiple views can share a single buffer.

    .. since:: 4081
    """

    buffer: sublime.Buffer

    def on_text_changed(self, changes: List[TextChange]):

        """Called once after changes has been made to a buffer, with detailed information
        about what has changed.
        """

    def on_text_changed_async(self, changes: List[TextChange]):

        """Same as `on_text_changed` but runs in a separate thread, not blocking the application."""

    def on_revert(self):

        """Called when the buffer is reverted.

        A revert does not trigger text changes. If the contents of the buffer
        are required here use `View.substr`."""

    def on_revert_async(self):

        """Same as `on_revert` but runs in a separate thread, not blocking the
        application.
        """

    def on_reload(self):

        """Called when the buffer is reloaded.

        A reload does not trigger text changes. If the contents of the buffer
        are required here use `View.substr`."""

    def on_reload_async(self):

        """Same as `on_reload` but runs in a separate thread, not blocking the
        application.
        """

    @classmethod
    def is_applicable(cls, buffer: sublime.Buffer):
        """
        :returns: Whether this listener should apply to the provided buffer.
        """

    def __init__(self):
        ...

    def detach(self):
        """
        Remove this listener from the buffer.

        Async callbacks may still be called after this, as they are queued
        separately.

        :raises ValueError: if the listener is not attached.
        """

    def attach(self, buffer: sublime.Buffer):
        """
        Attach this listener to a buffer.

        :raises ValueError: if the listener is already attached.
        """

    def is_attached(self) -> bool:
        """
        :returns:
            whether the listener is receiving events from a buffer. May not be
            called from ``__init__``.
        """


class MultizipImporter(importlib.abc.MetaPathFinder):
    """:meta private:"""

    def __init__(self):
        ...

    def find_spec(self, fullname, path, target=None):
        """
        :param fullname:
            A unicode string of the module name

        :param path:
            None or a list with a single unicode string of the __path__ of
            the parent module if importing a submodule

        :param target:
            Unused - extra info that importlib may provide?

        :return:
            An importlib.machinery.ModuleSpec() object
        """


class ZipResourceReader(importlib.abc.ResourceReader):
    """
    Implements the resource reader interface introduced in Python 3.7

    :meta private:
    """

    def __init__(self, loader, fullname):
        """
        :param loader:
            The source ZipLoader() object

        :param fullname:
            A unicode string of the module name to load resources for
        """

    def open_resource(self, resource):
        """
        :param resource:
            A unicode string of a resource name - should not contain a path
            separator

        :raises:
            FileNotFoundError - when the resource doesn't exist

        :return:
            An io.BytesIO() object
        """

    def resource_path(self, resource):
        """
        :param resource:
            A unicode string of a resource name - should not contain a path
            separator

        :raises:
            FileNotFoundError - always, since there is no normal filesystem access
        """

    def is_resource(self, name):
        """
        :param name:
            A unicode string of a file name to check if it is a resource

        :return:
            A boolean indicating if the file is a resource
        """

    def contents(self):
        """
        :return:
            A list of the resources for this module
        """


class ZipLoader(importlib.abc.InspectLoader):
    """
    A custom Python loader that handles loading .py and .pyc files from
    .sublime-package zip files, and supports overrides where a loose file in
    the Packages/ folder of the data dir may be loaded instead of a file in
    the .sublime-package file.

    :meta private:
    """

    def __init__(self, zippath):
        """
        :param zippath:
            A unicode string of the full filesystem path to the zip file
        """

    def has(self, fullname):
        """
        Checks if the module is handled by this loader

        :param fullname:
            A unicode string of the module to check

        :return:
            A boolean if the module is handled by this loader
        """

    def get_resource_reader(self, fullname):
        """
        :param fullname:
            A unicode string of the module name to get the resource reader for

        :return:
            None if the module is not a package, otherwise an object that
            implements the importlib.abc.ResourceReader() interface
        """

    def get_filename(self, fullname):
        """
        :param fullname:
            A unicode string of the module name

        :raises:
            ImportError - when the module has no file path

        :return:
            A unicode string of the file path to the module
        """

    def get_code(self, fullname):
        """
        :param fullname:
            A unicode string of the module to get the code for

        :raises:
            ModuleNotFoundError - when the module is not part of this zip file
            ImportError - when there is an error loading the code

        :return:
            A code object for the module
        """

    def get_source(self, fullname):
        """
        :param fullname:
            A unicode string of the module to get the source for

        :raises:
            ModuleNotFoundError - when the module is not part of this zip file
            ImportError - when there is an error loading the source file

        :return:
            A unicode string of the source code, or None if there is no source
            for the module (i.e. a .pyc file)
        """

    def is_package(self, fullname):
        """
        :param fullname:
            A unicode string of the module to see if it is a package

        :return:
            A boolean if the module is a package
        """


override_path: Any = ...
multi_importer: MultizipImporter = ...


def update_compressed_packages(pkgs):
    ...


def set_override_path(path):
    ...
