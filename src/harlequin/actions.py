from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from textual.screen import Screen
from textual_textarea.text_editor import TextAreaPlus

from harlequin.components import (
    CodeEditor,
    DataCatalog,
    EditorCollection,
    HarlequinTree,
    HistoryScreen,
    ResultsTable,
    ResultsViewer,
)
from harlequin.components.data_catalog import ContextMenu

if TYPE_CHECKING:
    from textual.widget import Widget

    from harlequin.config import CommandConfig


@dataclass
class Action:
    target: type[Widget] | None
    action: str
    description: str | None = None
    show: bool = False
    priority: bool = False


HARLEQUIN_ACTIONS = {
    #######################################################
    # APP ACTIONS
    #######################################################
    "quit": Action(
        target=None,
        action="quit",
        priority=True,
        show=True,
    ),
    "help": Action(
        target=None,
        action="show_help_screen",
        description="Help",
        show=True,
    ),
    "show_command_menu": Action(
        target=None,
        action="show_command_menu",
        description="Commands",
        priority=True,
    ),
    "focus_next": Action(target=Screen, action="focus_next"),
    "focus_previous": Action(target=Screen, action="focus_previous"),
    "focus_query_editor": Action(
        target=None,
        action="focus_query_editor",
    ),
    "focus_results_viewer": Action(
        target=None,
        action="focus_results_viewer",
    ),
    "focus_data_catalog": Action(
        target=None,
        action="focus_data_catalog",
    ),
    "toggle_sidebar": Action(
        target=None,
        action="toggle_sidebar",
    ),
    "toggle_catalog_side": Action(
        target=None,
        action="toggle_catalog_side",
        description="Move Data Catalog",
    ),
    "toggle_full_screen": Action(
        target=None,
        action="toggle_full_screen",
    ),
    "show_debug_info": Action(
        target=None,
        action="show_debug_info",
        description="Debug Info",
    ),
    "show_query_history": Action(
        target=None,
        action="show_query_history",
        description="History",
        show=True,
    ),
    "show_data_exporter": Action(
        target=None, action="export", description="Export Data"
    ),
    "refresh_catalog": Action(
        target=None, action="refresh_catalog", description="Refresh Data Catalog"
    ),
    "run_query": Action(target=None, action="run_query", description="Run Query"),
    # A directory another program drops queries and results into (harlequin.watch).
    # Not in the footer: it does nothing at all without `--watch-dir`, and a footer
    # slot is the scarcest thing on a half-screen pane.
    # priority, for the reason `launch_external_editor` has it: the editor is what
    # is focused when a query arrives, and a focused `TextArea` inserts any key
    # carrying a printable character -- a terminal's alt+i carries an "i".
    "open_watched": Action(
        target=None,
        action="open_watched",
        description="Open Watched Files",
        priority=True,
    ),
    "cancel_query": Action(
        target=None, action="cancel_query", description="Cancel Query"
    ),
    #######################################################
    # CodeEditor ACTIONS
    #######################################################
    "code_editor.new_buffer": Action(target=EditorCollection, action="new_buffer"),
    "code_editor.rename_buffer": Action(
        target=EditorCollection, action="rename_buffer", description="Rename Tab"
    ),
    "code_editor.close_buffer": Action(target=EditorCollection, action="close_buffer"),
    "code_editor.next_buffer": Action(target=EditorCollection, action="next_buffer"),
    # Sections: `-- ## Name` blocks in the buffer (roadmap §3.4). There is no
    # code folding to collapse the ones you are not reading, so these three are
    # what a long script gets: jump between the headings, open one on its own,
    # run one on its own.
    "code_editor.show_sections": Action(
        target=EditorCollection,
        action="show_sections",
        description="Sections",
        show=True,
    ),
    "code_editor.focus_section": Action(
        target=EditorCollection,
        action="focus_section",
        description="Focus Section",
    ),
    "code_editor.run_section": Action(
        target=EditorCollection,
        action="run_section",
        description="Run Section",
    ),
    "code_editor.run_query": Action(
        target=CodeEditor, action="submit", description="Run Query", show=True
    ),
    "code_editor.format_buffer": Action(
        target=CodeEditor, action="format", description="Format Query", show=True
    ),
    "code_editor.save_buffer": Action(
        target=CodeEditor, action="save", description="Save Query", show=True
    ),
    "code_editor.load_buffer": Action(
        target=CodeEditor, action="load", description="Open Query", show=True
    ),
    # priority: a focused TextArea inserts any key that carries a printable
    # character, and a terminal's alt+e carries an "e".
    "code_editor.launch_external_editor": Action(
        target=CodeEditor,
        action="launch_external_editor",
        description="Launch External Editor",
        priority=True,
    ),
    "code_editor.find": Action(
        target=CodeEditor, action="find", description="Find", show=True
    ),
    "code_editor.find_next": Action(
        target=CodeEditor, action="find(True)", description="Find Next", show=True
    ),
    "code_editor.goto_line": Action(
        target=CodeEditor, action="goto_line", description="Go To Line", show=True
    ),
    # Moving the cursor
    "code_editor.cursor_up": Action(target=TextAreaPlus, action="cursor_up"),
    "code_editor.cursor_down": Action(target=TextAreaPlus, action="cursor_down"),
    "code_editor.cursor_left": Action(target=TextAreaPlus, action="cursor_left"),
    "code_editor.cursor_right": Action(target=TextAreaPlus, action="cursor_right"),
    "code_editor.cursor_word_left": Action(
        target=TextAreaPlus, action="cursor_word_left"
    ),
    "code_editor.cursor_word_right": Action(
        target=TextAreaPlus, action="cursor_word_right"
    ),
    "code_editor.cursor_line_start": Action(
        target=TextAreaPlus, action="cursor_line_start"
    ),
    "code_editor.cursor_line_end": Action(
        target=TextAreaPlus, action="cursor_line_end"
    ),
    "code_editor.cursor_doc_start": Action(
        target=TextAreaPlus, action="cursor_doc_start"
    ),
    "code_editor.cursor_doc_end": Action(target=TextAreaPlus, action="cursor_doc_end"),
    "code_editor.cursor_page_up": Action(target=TextAreaPlus, action="cursor_page_up"),
    "code_editor.cursor_page_down": Action(
        target=TextAreaPlus, action="cursor_page_down"
    ),
    # Selecting
    "code_editor.select_up": Action(target=TextAreaPlus, action="cursor_up(True)"),
    "code_editor.select_down": Action(target=TextAreaPlus, action="cursor_down(True)"),
    "code_editor.select_left": Action(target=TextAreaPlus, action="cursor_left(True)"),
    "code_editor.select_right": Action(
        target=TextAreaPlus, action="cursor_right(True)"
    ),
    "code_editor.select_word_left": Action(
        target=TextAreaPlus, action="cursor_word_left(True)"
    ),
    "code_editor.select_word_right": Action(
        target=TextAreaPlus, action="cursor_word_right(True)"
    ),
    "code_editor.select_line_start": Action(
        target=TextAreaPlus, action="cursor_line_start(True)"
    ),
    "code_editor.select_line_end": Action(
        target=TextAreaPlus, action="cursor_line_end(True)"
    ),
    "code_editor.select_doc_start": Action(
        target=TextAreaPlus, action="cursor_doc_start(True)"
    ),
    "code_editor.select_doc_end": Action(
        target=TextAreaPlus, action="cursor_doc_end(True)"
    ),
    "code_editor.select_word": Action(target=TextAreaPlus, action="select_word"),
    "code_editor.select_line": Action(target=TextAreaPlus, action="select_line"),
    "code_editor.select_all": Action(target=TextAreaPlus, action="select_all"),
    # Scrolling (No cursor movement)
    "code_editor.scroll_up_one": Action(target=TextAreaPlus, action="scroll_one('up')"),
    "code_editor.scroll_down_one": Action(
        target=TextAreaPlus, action="scroll_one('down')"
    ),
    # Bulk Editing
    "code_editor.toggle_comment": Action(target=TextAreaPlus, action="toggle_comment"),
    "code_editor.cut": Action(target=TextAreaPlus, action="cut"),
    "code_editor.copy": Action(target=TextAreaPlus, action="copy"),
    "code_editor.paste": Action(target=TextAreaPlus, action="paste"),
    "code_editor.undo": Action(target=TextAreaPlus, action="undo"),
    "code_editor.redo": Action(target=TextAreaPlus, action="redo"),
    # Deleting
    "code_editor.delete_left": Action(target=TextAreaPlus, action="delete_left"),
    "code_editor.delete_right": Action(target=TextAreaPlus, action="delete_right"),
    "code_editor.delete_word_left": Action(
        target=TextAreaPlus, action="delete_word_left"
    ),
    "code_editor.delete_word_right": Action(
        target=TextAreaPlus, action="delete_word_right"
    ),
    "code_editor.delete_line": Action(target=TextAreaPlus, action="delete_line"),
    "code_editor.delete_to_start_of_line": Action(
        target=TextAreaPlus, action="delete_to_start_of_line"
    ),
    "code_editor.delete_to_end_of_line": Action(
        target=TextAreaPlus, action="delete_to_end_of_line"
    ),
    # Scoped duplicates of app actions
    "code_editor.focus_results_viewer": Action(
        target=EditorCollection, action="focus_results_viewer"
    ),
    "code_editor.focus_data_catalog": Action(
        target=EditorCollection, action="focus_data_catalog"
    ),
    # TODO: ADD AUTOCOMPLETE BINDINGS
    #######################################################
    # DataCatalog ACTIONS
    #######################################################
    "data_catalog.previous_tab": Action(
        target=DataCatalog, action="switch_tab(-1)", description="Previous Tab"
    ),
    "data_catalog.next_tab": Action(
        target=DataCatalog, action="switch_tab(1)", description="Next Tab"
    ),
    "data_catalog.insert_name": Action(
        target=HarlequinTree, action="submit", description="Insert Name", show=True
    ),
    "data_catalog.copy_name": Action(
        target=HarlequinTree, action="copy", description="Copy Name"
    ),
    "data_catalog.select_cursor": Action(target=HarlequinTree, action="select_cursor"),
    "data_catalog.toggle_node": Action(target=HarlequinTree, action="toggle_node"),
    "data_catalog.cursor_up": Action(target=HarlequinTree, action="cursor_up"),
    "data_catalog.cursor_down": Action(target=HarlequinTree, action="cursor_down"),
    # Scoped duplicates of app actions
    "data_catalog.focus_query_editor": Action(
        target=DataCatalog, action="focus_query_editor"
    ),
    "data_catalog.focus_results_viewer": Action(
        target=DataCatalog, action="focus_results_viewer"
    ),
    "data_catalog.show_context_menu": Action(
        target=HarlequinTree, action="show_context_menu", show=True
    ),
    "data_catalog.hide_context_menu": Action(target=ContextMenu, action="hide"),
    #######################################################
    # ResultsViewer ACTIONS
    #######################################################
    "results_viewer.previous_tab": Action(
        target=ResultsViewer, action="switch_tab(-1)", description="Previous Tab"
    ),
    "results_viewer.next_tab": Action(
        target=ResultsViewer, action="switch_tab(1)", description="Next Tab"
    ),
    "results_viewer.rename_tab": Action(
        target=ResultsViewer, action="rename_tab", description="Rename Tab"
    ),
    "results_viewer.pin_tab": Action(
        target=ResultsViewer, action="toggle_pin", description="Pin/Unpin Tab"
    ),
    "results_viewer.close_tab": Action(
        target=ResultsViewer, action="close_tab", description="Close Tab"
    ),
    "results_viewer.copy_selection": Action(
        target=ResultsTable, action="copy_selection"
    ),
    "results_viewer.view_cell": Action(
        target=ResultsTable, action="view_cell", description="View Cell"
    ),
    "results_viewer.show_columns": Action(
        target=ResultsTable, action="show_columns", description="List Columns"
    ),
    "results_viewer.select_cursor": Action(target=ResultsTable, action="select_cursor"),
    # Moving the cursor
    "results_viewer.cursor_up": Action(target=ResultsTable, action="cursor_up"),
    "results_viewer.cursor_down": Action(target=ResultsTable, action="cursor_down"),
    "results_viewer.cursor_left": Action(target=ResultsTable, action="cursor_left"),
    "results_viewer.cursor_right": Action(target=ResultsTable, action="cursor_right"),
    "results_viewer.cursor_row_start": Action(
        target=ResultsTable, action="cursor_row_start"
    ),
    "results_viewer.cursor_row_end": Action(
        target=ResultsTable, action="cursor_row_end"
    ),
    "results_viewer.cursor_column_start": Action(
        target=ResultsTable, action="scroll_home"
    ),
    "results_viewer.cursor_column_end": Action(
        target=ResultsTable, action="scroll_end"
    ),
    "results_viewer.cursor_next_cell": Action(
        target=ResultsTable, action="cursor_next"
    ),
    "results_viewer.cursor_previous_cell": Action(
        target=ResultsTable, action="cursor_prev"
    ),
    "results_viewer.cursor_page_up": Action(target=ResultsTable, action="page_up"),
    "results_viewer.cursor_page_down": Action(target=ResultsTable, action="page_down"),
    "results_viewer.cursor_table_start": Action(
        target=ResultsTable, action="cursor_table_start"
    ),
    "results_viewer.cursor_table_end": Action(
        target=ResultsTable, action="cursor_table_end"
    ),
    # Selecting cells
    "results_viewer.select_up": Action(target=ResultsTable, action="cursor_up(True)"),
    "results_viewer.select_down": Action(
        target=ResultsTable, action="cursor_down(True)"
    ),
    "results_viewer.select_left": Action(
        target=ResultsTable, action="cursor_left(True)"
    ),
    "results_viewer.select_right": Action(
        target=ResultsTable, action="cursor_right(True)"
    ),
    "results_viewer.select_row_start": Action(
        target=ResultsTable, action="cursor_row_start(True)"
    ),
    "results_viewer.select_row_end": Action(
        target=ResultsTable, action="cursor_row_end(True)"
    ),
    "results_viewer.select_column_start": Action(
        target=ResultsTable, action="scroll_home(True)"
    ),
    "results_viewer.select_column_end": Action(
        target=ResultsTable, action="scroll_end(True)"
    ),
    "results_viewer.select_page_up": Action(
        target=ResultsTable, action="page_up(True)"
    ),
    "results_viewer.select_page_down": Action(
        target=ResultsTable, action="page_down(True)"
    ),
    "results_viewer.select_table_start": Action(
        target=ResultsTable, action="cursor_table_start(True)"
    ),
    "results_viewer.select_table_end": Action(
        target=ResultsTable, action="cursor_table_end(True)"
    ),
    "results_viewer.select_all": Action(target=ResultsTable, action="select_all"),
    # Scoped duplicates of app actions
    "results_viewer.focus_query_editor": Action(
        target=ResultsViewer, action="focus_query_editor"
    ),
    "results_viewer.focus_data_catalog": Action(
        target=ResultsViewer, action="focus_data_catalog"
    ),
    #######################################################
    # HistoryScreen ACTIONS
    #######################################################
    "history_screen.select_query": Action(
        target=HistoryScreen, action="select", description="Select Query"
    ),
    "history_screen.cancel": Action(
        target=HistoryScreen, action="cancel", description="Cancel"
    ),
}


COMMAND_ACTION_PREFIX = "command."
"""The namespace an action from config lives in, so it cannot collide with
a built-in."""


def build_actions(
    commands: Mapping[str, "CommandConfig"] | None = None,
) -> dict[str, Action]:
    """Every action a keymap may bind: the built-ins, plus one per configured command.

    A command's action is app-level (`target=None`) so that it fires whichever pane has
    focus -- a command that sends the visible result has to work from the results grid,
    and one that sends the buffer has to work from the editor.

    `priority=True` for the same reason `launch_external_editor` has it: a focused
    `TextArea` swallows any key that carries a printable character, which is every
    `alt`-chord, and a binding that only worked when the editor was not focused would
    be a binding that mostly did nothing.
    """
    actions = dict(HARLEQUIN_ACTIONS)
    for name, command in (commands or {}).items():
        actions[f"{COMMAND_ACTION_PREFIX}{name}"] = Action(
            target=None,
            action=f"run_command('{name}')",
            description=command.description or f"Run {name}",
            priority=True,
        )
    return actions
