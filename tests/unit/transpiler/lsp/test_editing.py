import logging
import os
import re
from collections.abc import Callable, Sequence
from logging import LogRecord
from pathlib import Path
from typing import TypeAlias
from unittest.mock import Mock, DEFAULT

import pytest
from lsprotocol.types import (
    CreateFile,
    CreateFileOptions,
    DeleteFile,
    FailureHandlingKind,
    OptionalVersionedTextDocumentIdentifier,
    Position,
    Range,
    RenameFile,
    ResourceOperationKind,
    TextDocumentEdit,
    TextEdit,
    WorkspaceEdit,
    ApplyWorkspaceEditResult,
)

from databricks.labs.lakebridge.transpiler.lsp.editing import (
    BaseEditor,
    DocumentChange,
    EditorProxy,
    LakebridgeEditor,
    RetargetingEditor,
    SandboxEditor,
    logger as editing_logger,
    Editor,
)


LSP_ORIGIN = Range(start=Position(0, 0), end=Position(0, 0))


AppliedEdits: TypeAlias = tuple[str, Sequence[TextEdit]] | DocumentChange


class MinimumEditor(BaseEditor):
    edits: list[AppliedEdits]

    def __init__(self):
        self.edits = []

    def _apply_text_edits(self, uri: str, text_edits: Sequence[TextEdit]) -> ApplyWorkspaceEditResult:
        self.edits.append((uri, text_edits))
        return ApplyWorkspaceEditResult(applied=True)

    def _apply_document_edit(self, edit: TextDocumentEdit) -> ApplyWorkspaceEditResult:
        self.edits.append(edit)
        return ApplyWorkspaceEditResult(applied=True)

    def _create_file(self, edit: CreateFile) -> ApplyWorkspaceEditResult:
        self.edits.append(edit)
        return ApplyWorkspaceEditResult(applied=True)

    def _rename_file(self, edit: RenameFile) -> ApplyWorkspaceEditResult:
        self.edits.append(edit)
        return ApplyWorkspaceEditResult(applied=True)

    def _delete_file(self, edit: DeleteFile) -> ApplyWorkspaceEditResult:
        self.edits.append(edit)
        return ApplyWorkspaceEditResult(applied=True)


def test_default_capabilities() -> None:
    """Verify the default set of capabilities that are reported."""

    capabilities = MinimumEditor().capabilities()
    assert capabilities.normalizes_line_endings, "Line endings are normalized here to avoid duplication in servers."
    assert capabilities.failure_handling == FailureHandlingKind.Abort, "Editors abort on first error by default."
    assert capabilities.resource_operations == [], "No resource operations are supported by default."


def test_normalize_empty_string() -> None:
    """Empty string should remain empty."""
    assert BaseEditor.normalize_line_endings("") == ""


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        # DOS/Windows: CRLF -> LF
        ("line1\r\nline2\r\nline3\r\n", "line1\nline2\nline3\n"),
        # Classic MacOS: CR -> LF
        ("line1\rline2\rline3\r", "line1\nline2\nline3\n"),
        # Unix: already LF
        ("line1\nline2\nline3\n", "line1\nline2\nline3\n"),
        # Mixed, and no trailing EOL
        ("line1\r\nline2\rline3\nline4", "line1\nline2\nline3\nline4"),
    ),
)
def test_normalize_line_endings(source: str, expected: str) -> None:
    """Verify that lines can be normalized."""
    result = BaseEditor.normalize_line_endings(source)
    assert result == expected


class _RecordingBaseEditor(BaseEditor):
    """Minimal implementation of the BaseEditor that records events."""

    event_types = TextDocumentEdit | CreateFile | RenameFile | DeleteFile | tuple[str, Sequence[TextEdit]]
    events: list[event_types]

    def __init__(self) -> None:
        self.events = []

    def _apply_document_edit(self, edit: TextDocumentEdit) -> ApplyWorkspaceEditResult:
        self.events.append(edit)
        return ApplyWorkspaceEditResult(applied=True)

    def _create_file(self, edit: CreateFile) -> ApplyWorkspaceEditResult:
        self.events.append(edit)
        return ApplyWorkspaceEditResult(applied=True)

    def _rename_file(self, edit: RenameFile) -> ApplyWorkspaceEditResult:
        self.events.append(edit)
        return ApplyWorkspaceEditResult(applied=True)

    def _delete_file(self, edit: DeleteFile) -> ApplyWorkspaceEditResult:
        self.events.append(edit)
        return ApplyWorkspaceEditResult(applied=True)

    def _apply_text_edits(self, uri: str, text_edits: Sequence[TextEdit]) -> ApplyWorkspaceEditResult:
        self.events.append((uri, text_edits))
        return ApplyWorkspaceEditResult(applied=True)


def assert_base_editor_events(edit: WorkspaceEdit, expected_events: Sequence[_RecordingBaseEditor.event_types]) -> None:
    editor = _RecordingBaseEditor()
    result = editor.apply(edit)
    assert result.applied and editor.events == list(expected_events)


def test_base_editor_empty_edit() -> None:
    """Verify that an empty edit is handled with success."""
    trivial_edit = WorkspaceEdit()
    assert_base_editor_events(trivial_edit, expected_events=())


def test_base_editor_applies_changes() -> None:
    """Verify that simple (non-document) changes from the edit are applied."""
    changes: dict[str, Sequence[TextEdit]] = {
        "foo": (
            TextEdit(range=Range(start=Position(1, 2), end=Position(3, 4)), new_text="bar"),
            TextEdit(Range(Position(5, 6), Position(7, 8)), "daz"),
        ),
        "baz": (),
        "diz": (TextEdit(Range(Position(9, 1), Position(2, 3)), "fiz"),),
    }
    assert_base_editor_events(WorkspaceEdit(changes), expected_events=list(changes.items()))


def test_base_editor_applies_document_changes() -> None:
    """Verify that document changes from the edit are applied."""
    document_changes: Sequence[TextDocumentEdit | CreateFile | RenameFile | DeleteFile] = (
        CreateFile(uri="foo"),
        TextDocumentEdit(
            OptionalVersionedTextDocumentIdentifier("foo"),
            edits=(
                TextEdit(range=LSP_ORIGIN, new_text="BAR"),
                TextEdit(range=LSP_ORIGIN, new_text="FOO"),
            ),
        ),
        RenameFile(old_uri="foo", new_uri="bar"),
        DeleteFile(uri="bar"),
    )
    assert_base_editor_events(WorkspaceEdit(document_changes=document_changes), expected_events=document_changes)


def test_base_editor_document_changes_over_simple_changes() -> None:
    """Verify that document changes have priority over simple changes."""
    document_changes = (CreateFile(uri="foo"), RenameFile(old_uri="foo", new_uri="bar"), DeleteFile(uri="bar"))
    ignored_changes = {"fizz": (TextEdit(Range(Position(0, 0), Position(0, 0)), "buzz"),)}
    assert_base_editor_events(
        WorkspaceEdit(changes=ignored_changes, document_changes=document_changes), expected_events=document_changes
    )


def test_base_editor_changes_error_abort() -> None:
    """Verify that an error while applying simple changes leads to an abort."""
    simple_changes = {
        "foo": (
            TextEdit(range=Range(start=Position(1, 2), end=Position(3, 4)), new_text="bar"),
            TextEdit(Range(Position(5, 6), Position(7, 8)), "baz"),
        ),
        "daz": (),
        # Next edit will fail to apply:
        "diz": (TextEdit(Range(Position(9, 1), Position(2, 3)), "fiz"),),
        "gaz": (TextEdit(Range(Position(4, 5), Position(6, 7)), "haz"),),
    }
    editor = _RecordingBaseEditor()
    setattr(
        editor,
        "_apply_text_edits",
        Mock(
            wraps=editor._apply_text_edits,
            side_effect=[
                DEFAULT,
                DEFAULT,
                ApplyWorkspaceEditResult(applied=False, failure_reason="Simulated failure"),
            ],
        ),
    )

    result = editor.apply(WorkspaceEdit(simple_changes))

    assert result == ApplyWorkspaceEditResult(applied=False, failure_reason="Simulated failure", failed_change=2)
    # Only the first 2 events are recorded.
    assert editor.events == list(simple_changes.items())[:2]


def test_base_editor_document_changes_error_abort() -> None:
    """Verify that an error while applying document changes leads to an abort."""
    document_changes = (
        CreateFile(uri="foo"),
        TextDocumentEdit(
            OptionalVersionedTextDocumentIdentifier("foo"),
            edits=(TextEdit(range=LSP_ORIGIN, new_text="BAR"), TextEdit(range=LSP_ORIGIN, new_text="FOO")),
        ),
        # Next edit will fail to apply:
        RenameFile(old_uri="foo", new_uri="bar"),
        DeleteFile(uri="bar"),
    )
    editor = _RecordingBaseEditor()
    setattr(
        editor, "_rename_file", lambda edit: ApplyWorkspaceEditResult(applied=False, failure_reason="Simulated failure")
    )

    result = editor.apply(WorkspaceEdit(document_changes=document_changes))

    assert result == ApplyWorkspaceEditResult(applied=False, failure_reason="Simulated failure", failed_change=2)
    # Only the first 2 events are recorded.
    assert editor.events == list(document_changes[:2])


def test_lakebridge_editor_capabilities() -> None:
    """Verify the capabilities declared by the Lakebridge editor."""
    capabilities = LakebridgeEditor().capabilities()
    assert capabilities.document_changes
    assert capabilities.normalizes_line_endings
    assert (resource_operations := capabilities.resource_operations) is not None
    expected_resource_operations = {ResourceOperationKind.Create}
    assert len(resource_operations) == len(expected_resource_operations)
    assert set(resource_operations) == expected_resource_operations


def test_empty_edit_success() -> None:
    """Verify that a trivial empty no-op edit succeeds."""
    editor = LakebridgeEditor()
    trivial_edit = WorkspaceEdit()
    result = editor.apply(trivial_edit)
    assert result.applied and result.failure_reason is None


def test_create_file(tmp_path: Path) -> None:
    """Verify that simple file creation works."""
    file_to_create = tmp_path / "a_file.txt"
    edit = WorkspaceEdit(document_changes=(CreateFile(uri=file_to_create.as_uri()),))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied and file_to_create.read_text(encoding="utf-8") == ""


def test_create_file_error_if_exists(tmp_path: Path) -> None:
    """Verify that simple file creation won't overwrite an existing file."""
    existing_file = tmp_path / "do_not_overwrite.txt"
    existing_file.write_text("This file will not be overwritten.", encoding="utf-8")
    edit = WorkspaceEdit(document_changes=(CreateFile(uri=existing_file.as_uri()),))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert (
        not result.applied and result.failure_reason == f"Cannot create file, already exists: {existing_file.as_uri()}"
    )
    assert existing_file.read_text("utf-8") == "This file will not be overwritten."


def test_create_nested_file(tmp_path: Path) -> None:
    """Verify that file creation works in a nested location, where the parent directories don't exist."""
    file_to_create = tmp_path / "nested" / "deeply" / "a_file.txt"
    edit = WorkspaceEdit(document_changes=(CreateFile(uri=file_to_create.as_uri()),))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied and file_to_create.read_text("utf-8") == ""


def test_create_file_ignore_if_exists(tmp_path: Path) -> None:
    """Verify that the creating a file can ignore an existing one."""
    a_new_file = tmp_path / "new_file.txt"
    existing_file = tmp_path / "existing_file.txt"
    existing_file.write_text("This file will not be overwritten.", encoding="utf-8")
    options = CreateFileOptions(ignore_if_exists=True)
    edit = WorkspaceEdit(
        document_changes=(
            CreateFile(uri=a_new_file.as_uri(), options=options),
            CreateFile(uri=existing_file.as_uri(), options=options),
        )
    )

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied
    assert a_new_file.read_text("utf-8") == ""
    assert existing_file.read_text("utf-8") == "This file will not be overwritten."


def test_create_file_overwrite(tmp_path: Path) -> None:
    """Verify that the creating a file can force overwriting an existing one."""
    new_file = tmp_path / "new_file.txt"
    existing_file = tmp_path / "existing_file.txt"
    existing_file.write_text("This file will be truncated.", encoding="utf-8")
    options = CreateFileOptions(overwrite=True)
    edit = WorkspaceEdit(
        document_changes=(
            CreateFile(uri=new_file.as_uri(), options=options),
            CreateFile(uri=existing_file.as_uri(), options=options),
        )
    )

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied
    assert new_file.read_text("utf-8") == ""
    assert existing_file.read_text("utf-8") == ""


def test_create_file_overwrite_vs_ignore(tmp_path: Path) -> None:
    """Verify that when creating a file the overwriting flags trumps the flag to ignore an existing file."""
    existing_file = tmp_path / "existing_file.txt"
    existing_file.write_text(
        "This file will be truncated, even though the ignore_if_exists flag is set.", encoding="utf-8"
    )
    options = CreateFileOptions(overwrite=True, ignore_if_exists=True)
    edit = WorkspaceEdit(document_changes=(CreateFile(uri=existing_file.as_uri(), options=options),))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied and existing_file.read_text("utf-8") == ""


def _write_file(
    target: Path, content: str, *, options: CreateFileOptions | None
) -> Sequence[CreateFile | TextDocumentEdit]:
    target_uri = target.as_uri()
    return (
        CreateFile(uri=target_uri, options=options),
        TextDocumentEdit(
            OptionalVersionedTextDocumentIdentifier(uri=target_uri),
            edits=(TextEdit(range=LSP_ORIGIN, new_text=content),),
        ),
    )


def new_file(target: Path, content: str) -> Sequence[CreateFile | TextDocumentEdit]:
    return _write_file(target, content, options=None)


def replace_file(target: Path, content: str) -> Sequence[CreateFile | TextDocumentEdit]:
    return _write_file(target, content, options=CreateFileOptions(overwrite=True))


def test_simple_create_with_content(tmp_path: Path) -> None:
    """Verify that a file can be created and populated with content."""
    file_to_create = tmp_path / "nested" / "new_file.txt"
    edit = WorkspaceEdit(document_changes=new_file(file_to_create, "Content for file."))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied and file_to_create.read_text(encoding="utf-8") == "Content for file."


def test_simple_replace_file(tmp_path: Path) -> None:
    """Verify that a file can be replaced with new content."""
    file_to_overwrite = tmp_path / "existing_file.txt"
    file_to_overwrite.write_text("Content prior to overwriting.", encoding="utf-8")
    edit = WorkspaceEdit(document_changes=replace_file(file_to_overwrite, "Overwritten content of file."))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied and file_to_overwrite.read_text(encoding="utf-8") == "Overwritten content of file."


def test_created_file_encoding(tmp_path: Path) -> None:
    """Verify that files are written as UTF-8."""
    file_to_write = tmp_path / "a_file.txt"
    edit = WorkspaceEdit(document_changes=new_file(file_to_write, "Some text with unicode: \U0001f9e1"))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied and file_to_write.read_text(encoding="utf-8") == "Some text with unicode: \N{ORANGE HEART}"


def test_create_multiple_files(tmp_path: Path) -> None:
    """Verify that we can create multiple files within a single edit."""
    file_1 = tmp_path / "file_1.txt"
    file_2 = tmp_path / "file_2.txt"
    file_3 = tmp_path / "file_3.txt"
    edit = WorkspaceEdit(
        document_changes=(
            *new_file(file_1, "Content for file 1."),
            *new_file(file_2, "Content for file 2."),
            *new_file(file_3, "Content for file 3."),
        )
    )

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied
    assert file_1.read_text(encoding="utf-8") == "Content for file 1."
    assert file_2.read_text(encoding="utf-8") == "Content for file 2."
    assert file_3.read_text(encoding="utf-8") == "Content for file 3."


def test_create_file_overwrites_same_file(tmp_path: Path) -> None:
    """Verify that we properly handle overwriting the same file multiple times within an edit."""
    the_file = tmp_path / "nested" / "the_file.txt"
    edit = WorkspaceEdit(
        document_changes=(
            *new_file(the_file, "First content for file."),
            CreateFile(the_file.as_uri(), options=CreateFileOptions(overwrite=True)),
            *replace_file(the_file, "Ultimate content."),
        )
    )

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied and the_file.read_text(encoding="utf-8") == "Ultimate content."


def test_create_multiple_files_interspersed(tmp_path: Path) -> None:
    """Verify that when writing to multiple files we don't care if the create events aren't next to the inserts."""
    file_1 = tmp_path / "nested" / "file_1.txt"
    file_2 = tmp_path / "nested" / "file_2.txt"
    file_3 = tmp_path / "nested" / "file_3.txt"
    files = (file_1, file_2, file_3)
    edit = WorkspaceEdit(
        document_changes=(
            # First all the CreateFile events.
            *[CreateFile(f.as_uri()) for f in files],
            # Then the events that insert the content.
            *[
                TextDocumentEdit(
                    OptionalVersionedTextDocumentIdentifier(uri=f.as_uri()),
                    edits=(TextEdit(range=LSP_ORIGIN, new_text=f"Content of file: {f.name}"),),
                )
                for f in files
            ],
        )
    )

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied
    assert file_1.read_text("utf-8") == "Content of file: file_1.txt"
    assert file_2.read_text("utf-8") == "Content of file: file_2.txt"
    assert file_3.read_text("utf-8") == "Content of file: file_3.txt"


def test_split_write_file(tmp_path: Path) -> None:
    """Verify that the create/insert events don't need to be part of the same LSP event."""
    a_file = tmp_path / "a_file.txt"
    edits = (
        # First edit event just creates the file.
        WorkspaceEdit(document_changes=(CreateFile(a_file.as_uri()),)),
        # Subsequent edit event writes the content to the file.
        WorkspaceEdit(
            document_changes=(
                TextDocumentEdit(
                    OptionalVersionedTextDocumentIdentifier(a_file.as_uri()),
                    edits=(TextEdit(LSP_ORIGIN, "Deferred content of file."),),
                ),
            )
        ),
    )

    editor = LakebridgeEditor()
    responses = [editor.apply(edit) for edit in edits]

    assert all(response.applied for response in responses)
    assert a_file.read_text("utf-8") == "Deferred content of file."


def test_edit_without_create_fails(tmp_path: Path) -> None:
    """Verify that an edit is only allowed after a creation event."""
    a_new_file = tmp_path / "a_file.txt"
    existing_file = tmp_path / "existing_file.txt"
    existing_file.write_text("An existing file.", encoding="utf-8")
    empty_file = tmp_path / "empty_file.txt"
    empty_file.touch()

    files = (a_new_file, existing_file, empty_file)
    edits = [
        WorkspaceEdit(
            document_changes=(
                TextDocumentEdit(
                    OptionalVersionedTextDocumentIdentifier(file.as_uri()),
                    edits=(TextEdit(LSP_ORIGIN, "An insertion that will fail."),),
                ),
            )
        )
        for file in files
    ]
    editor = LakebridgeEditor()
    results = [editor.apply(edit) for edit in edits]

    assert all(not result.applied for result in results)
    assert all(
        result.failure_reason is not None
        and result.failure_reason.startswith("Cannot modify a text document that is not newly created")
        for result in results
    )


@pytest.mark.parametrize(
    "disallowed_edits",
    (
        (TextEdit(Range(Position(0, 0), Position(1000, 0)), "Range replacement rather than origin insertion."),),
        (
            # This first edit is fine.
            TextEdit(Range(Position(0, 0), Position(0, 0)), new_text="First line\n"),
            # But this isn't allowed.
            TextEdit(Range(Position(1, 0), Position(1, 0)), new_text="Second line\n"),
        ),
    ),
)
def test_non_origin_insertion_fails(disallowed_edits: Sequence[TextEdit], tmp_path: Path) -> None:
    """Verify that disallowed edits are rejected."""
    a_new_file = tmp_path / "a_file.txt"
    edit = WorkspaceEdit(
        document_changes=(
            CreateFile(a_new_file.as_uri()),
            TextDocumentEdit(OptionalVersionedTextDocumentIdentifier(a_new_file.as_uri()), edits=disallowed_edits),
        ),
    )

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert not result.applied and result.failure_reason is not None
    assert "only a single insert at the start of the file is supported" in result.failure_reason


def test_create_with_line_ending_normalization(tmp_path: Path) -> None:
    """Confirm that lines are normalized when writing a file out."""
    the_file = tmp_path / "the_file.txt"
    edit = WorkspaceEdit(document_changes=new_file(the_file, "line 1\rline 2\r\nline 3\nline 4\n"))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied and the_file.read_text(encoding="utf-8") == "line 1\nline 2\nline 3\nline 4\n"


def test_path_normalisation(tmp_path: Path) -> None:
    """Verify that paths are normalized, to the extent that this is possible."""
    the_file = tmp_path / "the_file.txt"
    alias = tmp_path / "something" / "nested" / ".." / ".." / the_file.name
    edit = WorkspaceEdit(
        document_changes=(
            CreateFile(alias.as_uri()),
            TextDocumentEdit(
                OptionalVersionedTextDocumentIdentifier(the_file.as_uri()),
                edits=(TextEdit(LSP_ORIGIN, new_text="Content for the file."),),
            ),
        )
    )

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert result.applied and the_file.read_text(encoding="utf-8") == "Content for the file."


def test_rename_file_rejection(tmp_path: Path) -> None:
    """Verify that attempts to rename a file are rejected."""
    old_name = tmp_path / "before.txt"
    old_name.touch()
    new_name = tmp_path / "after.txt"
    edit = WorkspaceEdit(document_changes=(RenameFile(old_uri=old_name.as_uri(), new_uri=new_name.as_uri()),))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert not result.applied
    assert result.failure_reason == f"Renaming files is not supported: {old_name.as_uri()} -> {new_name.as_uri()}"


def test_delete_file_rejection(tmp_path: Path) -> None:
    """Verify that attempts to rename a file are rejected."""
    a_file = tmp_path / "a_file.txt"
    a_file.touch()
    edit = WorkspaceEdit(document_changes=(DeleteFile(a_file.as_uri()),))

    editor = LakebridgeEditor()
    result = editor.apply(edit)

    assert not result.applied
    assert result.failure_reason == f"Deleting files is not supported: {a_file.as_uri()}"


def _apply_failing_document_change(change: CreateFile, caplog: pytest.LogCaptureFixture) -> tuple[str, Sequence[str]]:
    """Apply a change to create a file that will fail, returning the failure reason and log warnings."""
    edit = WorkspaceEdit(document_changes=(change,))

    editor = LakebridgeEditor()
    with caplog.at_level(logging.WARNING):
        result = editor.apply(edit)

    assert not result.applied and result.failure_reason is not None
    editor_warning_messages = [
        record.message
        for record in caplog.records
        if record.levelno == logging.WARNING and record.name == editing_logger.name
    ]
    return result.failure_reason, editor_warning_messages


def test_create_file_mkdir_io_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Verify error handling if a mkdir() error occurs while creating/truncating a file."""
    # We can trigger a mkdir() failure by placing a file with the name of a parent directory.
    blocking_file = tmp_path / "blocking_file"
    blocking_file.touch()
    will_fail = blocking_file / "parent_cannot_be_created.txt"

    failure_reason, editor_warning_messages = _apply_failing_document_change(CreateFile(uri=will_fail.as_uri()), caplog)

    assert "parent directory could not be created" in failure_reason
    assert any(w.startswith("Cannot create/truncate file") for w in editor_warning_messages)


def test_create_file_open_io_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Verify error handling if an error occurs while opening a file to create/truncate it."""
    # We can trigger a failure in .open() by having the file already exist as a directory.
    blocking_directory = tmp_path / "blocking_dir"
    blocking_directory.mkdir()
    will_fail = blocking_directory
    change = CreateFile(uri=will_fail.as_uri(), options=CreateFileOptions(overwrite=True))

    failure_reason, editor_warning_messages = _apply_failing_document_change(change, caplog)

    assert failure_reason.startswith("Cannot create/truncate file")
    assert any(w.startswith("Cannot create/truncate file") for w in editor_warning_messages)


class LakebridgeEditorFriend(LakebridgeEditor):
    def __init__(self, *, write_buffering: int) -> None:
        super().__init__(write_buffering=write_buffering)

    def force_close(self, path: Path) -> None:
        """Force a close of the underlying file, without python being aware.

        Subsequent operations will fail with OS errors."""
        fd = self._open_files[path].fileno()
        os.close(fd)


def test_edit_file_write_io_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Verify error handling if an error occurs while writing the content to a file."""
    # To trigger this, we:
    #  - Invalidate the underlying file descriptor, which will trigger an error when writing to it.
    #  - Ensure we write more content than fits in Python's write buffer.
    # This ensures we get an OS error during the .write() call.
    buffer_size = 4096  # Needs to be large-ish, otherwise the python subsystem effectively ignores it.
    content_size = 2 * buffer_size

    only_log = _apply_failing_document_write(tmp_path, buffer_size, content_size, caplog)
    assert only_log.exc_text is not None
    # Sanity checks, based on the traceback text:
    #   - Should be a line indicating it came from the .write() call.
    assert "open_file.write(normalized_text)" in only_log.exc_text, "Error not handled due to .write() failure."


def test_edit_file_close_io_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Verify error handling if an error occurs while closing() the file."""
    # To trigger this, we:
    #  - Invalidate the underlying file descriptor, which will trigger an error when writing to it.
    #  - Ensure we write less content than fits in Python's write buffer: writing will be deferred until during .close()
    # This ensures we get an OS error during the .close() call.
    buffer_size = 4096
    content_size = buffer_size // 2

    only_log = _apply_failing_document_write(tmp_path, buffer_size, content_size, caplog)
    assert only_log.exc_text is not None
    # Sanity checks, based on the traceback text:
    #   - Should be a line showing it came from the with clause.
    #   - Should _not_ be a line indicating it came from the .write() call.
    assert "self._open_files.pop(path) as open_file" in only_log.exc_text, "Error not handled due to .close() failure."
    assert "open_file.write(normalized_text)" not in only_log.exc_text, "Error not handled due to .close() failure."


def _apply_failing_document_write(
    tmp_path: Path,
    buffer_size: int,
    content_size: int,
    caplog: pytest.LogCaptureFixture,
) -> LogRecord:
    """Apply a failing document write, returning the log warning generated."""
    the_file = tmp_path / "the_file.txt"
    create_change, write_change = new_file(the_file, 'x' * content_size)

    # Set up the editor for the failure to occur: we've closed the underlying file from underneath Python.
    editor = LakebridgeEditorFriend(write_buffering=buffer_size)
    assert editor.apply(WorkspaceEdit(document_changes=[create_change])).applied
    editor.force_close(the_file)

    # Perform the actual test.
    with caplog.at_level(logging.WARNING):
        result = editor.apply(WorkspaceEdit(document_changes=[write_change]))

    assert not result.applied and result.failure_reason is not None
    assert result.failure_reason.startswith("Cannot modify file")

    # Find the warning associated with the failure.
    editor_warning_logs = [
        record for record in caplog.records if record.levelno == logging.WARNING and record.name == editing_logger.name
    ]
    expected_message = f"Cannot modify file due to error: {the_file.as_uri()}"
    [only_log] = [record for record in editor_warning_logs if record.msg == expected_message]
    return only_log


def test_proxy_capabilities_are_underlying_capabilities() -> None:
    """Verify that the editor proxy simply returns the underlying editor's capabilities as its own."""

    class _IdentityProxy(EditorProxy):
        # Nothing needed here; base implementation is sufficient.
        pass

    base_editor = MinimumEditor()
    editor_proxy = _IdentityProxy(base_editor)

    assert base_editor.capabilities() == editor_proxy.capabilities()


# Synthetic path, a sandbox within which changes are allowed.
SANDBOX = Path("/path") / "to" / "sandbox"
# Various paths that are all within the sandbox.
ALLOWED_PATHS: Sequence[Path] = (
    SANDBOX / "file.txt",
    SANDBOX / "nested" / "file.txt",
    SANDBOX / "nested" / ".." / "file.txt",
    SANDBOX / "a" / "b" / ".." / ".." / "c" / "file.txt",
)
# Various paths that are all outside the sandbox.
DISALLOWED_PATHS: Sequence[Path] = (
    Path("/path") / "to" / "outside.txt",
    Path("/other") / "location.txt",
    SANDBOX / ".." / "outside.txt",
    SANDBOX / "nested" / ".." / ".." / "outside.txt",
    SANDBOX / ".." / ".." / ".." / "etc" / "passwd",
)


def _apply_sandbox_edits(edit: WorkspaceEdit) -> tuple[ApplyWorkspaceEditResult, Sequence[AppliedEdits]]:
    base_editor = MinimumEditor()
    sandbox_editor = SandboxEditor(base_editor, base=SANDBOX)

    result = sandbox_editor.apply(edit)

    return result, base_editor.edits


@pytest.mark.parametrize("allowed_path", ALLOWED_PATHS, ids=str)
def test_sandbox_editor_allows_changes_within_sandbox(allowed_path) -> None:
    """Verify that simple changes within the sandbox directory are allowed."""
    uri = allowed_path.as_uri()
    changes = (TextEdit(range=Range(start=Position(0, 0), end=Position(1, 0)), new_text="new first line\n"),)
    result, applied_edits = _apply_sandbox_edits(WorkspaceEdit(changes={uri: changes}))

    assert result.applied
    assert applied_edits == [(uri, changes)]


@pytest.mark.parametrize("disallowed_path", DISALLOWED_PATHS, ids=str)
def test_sandbox_editor_rejects_changes_outside_sandbox(disallowed_path: Path) -> None:
    """Verify that simple changes outside the sandbox are rejected."""
    uri = disallowed_path.as_uri()
    changes = (TextEdit(range=Range(start=Position(0, 0), end=Position(1, 0)), new_text="new first line\n"),)

    result, applied_edits = _apply_sandbox_edits(WorkspaceEdit(changes={uri: changes}))

    assert not result.applied
    assert result.failure_reason is not None and "must be within" in result.failure_reason
    assert not applied_edits


def _create_file(p: Path) -> CreateFile:
    return CreateFile(uri=p.as_uri(), options=CreateFileOptions(overwrite=True))


def _delete_file(p: Path) -> DeleteFile:
    return DeleteFile(uri=p.as_uri())


def _rename_file(old: Path, new: Path) -> RenameFile:
    return RenameFile(old.as_uri(), new.as_uri())


def _text_replace(p: Path, new_text="replacement first line\n") -> TextDocumentEdit:
    text_document = OptionalVersionedTextDocumentIdentifier(uri=p.as_uri())
    edits = (TextEdit(range=LSP_ORIGIN, new_text=new_text),)
    return TextDocumentEdit(text_document=text_document, edits=edits)


def _test_sandbox_document_changes_allowed(document_changes: Sequence[DocumentChange]) -> None:
    result, applied_edits = _apply_sandbox_edits(WorkspaceEdit(document_changes=document_changes))

    assert result.applied
    assert applied_edits == list(document_changes)


def _test_sandbox_document_changes_rejected(document_changes: Sequence[DocumentChange]) -> None:
    result, applied_edits = _apply_sandbox_edits(WorkspaceEdit(document_changes=document_changes))

    assert not result.applied
    assert result.failure_reason is not None and "must be within" in result.failure_reason
    assert not applied_edits


@pytest.mark.parametrize("allowed_path", ALLOWED_PATHS, ids=str)
@pytest.mark.parametrize("resource_op", (_create_file, _delete_file, _text_replace))
def test_sandbox_editor_allows_single_resource_operations_within_sandbox(
    allowed_path: Path, resource_op: Callable[[Path], DocumentChange]
) -> None:
    """Verify that single-resource operations outside the sandbox are allowed."""
    document_changes = (resource_op(allowed_path),)
    _test_sandbox_document_changes_allowed(document_changes)


@pytest.mark.parametrize("allowed_old_path", ALLOWED_PATHS, ids=str)
@pytest.mark.parametrize("allowed_new_path", ALLOWED_PATHS, ids=str)
def test_sandbox_editor_allows_rename_file_within_sandbox(allowed_old_path: Path, allowed_new_path: Path) -> None:
    """Verify that renaming within the sandbox is allowed."""
    document_changes = (_rename_file(allowed_old_path, allowed_new_path),)
    _test_sandbox_document_changes_allowed(document_changes)


@pytest.mark.parametrize("disallowed_path", DISALLOWED_PATHS, ids=str)
@pytest.mark.parametrize("resource_op", (_create_file, _delete_file, _text_replace))
def test_sandbox_editor_rejects_single_resource_operations_outside_sandbox(
    disallowed_path: Path, resource_op: Callable[[Path], DocumentChange]
) -> None:
    """Verify that single-resource operations within the sandbox are rejected."""
    document_changes = (resource_op(disallowed_path),)
    _test_sandbox_document_changes_rejected(document_changes)


@pytest.mark.parametrize("disallowed_old_path", DISALLOWED_PATHS, ids=str)
def test_sandbox_editor_rejects_rename_file_from_outside_sandbox(disallowed_old_path: Path) -> None:
    """Verify that renaming from outside into the sandbox is not allowed."""
    inside_sandbox = SANDBOX / "file_within.txt"
    document_changes = (_rename_file(old=disallowed_old_path, new=inside_sandbox),)
    _test_sandbox_document_changes_rejected(document_changes)


@pytest.mark.parametrize("disallowed_new_path", DISALLOWED_PATHS, ids=str)
def test_sandbox_editor_rejects_rename_file_to_outside_sandbox(disallowed_new_path: Path) -> None:
    """Verify that renaming to outside the sandbox is not allowed."""
    inside_sandbox = SANDBOX / "file_within.txt"
    document_changes = (_rename_file(old=inside_sandbox, new=disallowed_new_path),)
    _test_sandbox_document_changes_rejected(document_changes)


@pytest.mark.parametrize("disallowed_old_path", DISALLOWED_PATHS, ids=str)
@pytest.mark.parametrize("disallowed_new_path", DISALLOWED_PATHS, ids=str)
def test_sandbox_editor_rejects_renames_outside_sandbox(disallowed_old_path: Path, disallowed_new_path: Path) -> None:
    """Verify that renaming from the sandbox to outside is not allowed."""
    document_changes = (_rename_file(old=disallowed_old_path, new=disallowed_new_path),)
    _test_sandbox_document_changes_rejected(document_changes)


RETARGET_BASE = Path("/path/to/input")
RETARGET_TARGET = Path("/path/output")

EXPECTED_RETARGETS = {
    RETARGET_BASE / "file.txt": RETARGET_TARGET / "file.txt",
    RETARGET_BASE / "path" / "to" / "file.txt": RETARGET_TARGET / "path" / "to" / "file.txt",
    RETARGET_BASE / "nested" / "but" / ".." / ".." / "not-nested.txt": RETARGET_TARGET / "not-nested.txt",
    RETARGET_BASE / "nested" / ".." / ".." / "not-retargeted": (RETARGET_BASE / ".." / "not-retargeted").resolve(),
    Path("/outside") / "non-retargeted.txt": Path("/outside") / "non-retargeted.txt",
}


def _apply_retargeted_edits(edit: WorkspaceEdit) -> tuple[ApplyWorkspaceEditResult, Sequence[AppliedEdits]]:
    base_editor = MinimumEditor()
    retargeting_editor = RetargetingEditor(base_editor, base=RETARGET_BASE, target=RETARGET_TARGET)

    result = retargeting_editor.apply(edit)

    return result, base_editor.edits


@pytest.mark.parametrize(("original_path", "expected_path"), EXPECTED_RETARGETS.items(), ids=str)
def test_retargeting_editor_changes(original_path: Path, expected_path: Path) -> None:
    """Verify that simple changes are retargeted properly."""
    text_edits = (TextEdit(range=Range(start=Position(0, 0), end=Position(1, 0)), new_text="replacement first line\n"),)
    original_changes = {original_path.as_uri(): text_edits}
    expected_changes = [(expected_path.as_uri(), text_edits)]

    result, edits = _apply_retargeted_edits(WorkspaceEdit(changes=original_changes))

    assert result.applied
    assert edits == expected_changes


def _test_retargeted_document_changes(
    document_changes: Sequence[DocumentChange], expected_changes: Sequence[DocumentChange]
) -> None:
    result, applied_edits = _apply_retargeted_edits(WorkspaceEdit(document_changes=document_changes))

    assert result.applied
    assert applied_edits == list(expected_changes)


@pytest.mark.parametrize(("original_path", "expected_path"), EXPECTED_RETARGETS.items(), ids=str)
@pytest.mark.parametrize("resource_op", (_create_file, _delete_file, _text_replace))
def test_retargeting_editor_single_resource_operations(
    original_path: Path, expected_path: Path, resource_op: Callable[[Path], DocumentChange]
) -> None:
    """Verify that single-resource operations are properly retargeted."""
    original_changes = (resource_op(original_path),)
    retargeted_changes = (resource_op(expected_path),)

    _test_retargeted_document_changes(original_changes, retargeted_changes)


@pytest.mark.parametrize(("original_old_path", "expected_old_path"), EXPECTED_RETARGETS.items(), ids=str)
@pytest.mark.parametrize(("original_new_path", "expected_new_path"), EXPECTED_RETARGETS.items(), ids=str)
def test_retargeting_editor_rename_operations(
    original_old_path: Path, expected_old_path: Path, original_new_path: Path, expected_new_path: Path
) -> None:
    """Verify that rename operations work properly, including when one side is outside the retargeting base."""
    original_changes = (_rename_file(original_old_path, original_new_path),)
    retargeted_changes = (_rename_file(expected_old_path, expected_new_path),)

    _test_retargeted_document_changes(original_changes, retargeted_changes)


def test_retargeting_lakebridge_editor() -> None:
    """Test that we can construct a retargeting lakebridge editor."""
    base = Path("/some/base")
    target = Path("/somewhere/else")
    editor: Editor = LakebridgeEditor.retargeting_editor(base=base, target=target)

    assert editor


@pytest.mark.parametrize(
    "inside_path",
    (
        Path("inside"),
        Path("also") / "inside",
        Path("tricky") / "but" / ".." / ".." / "still" / "inside",
    ),
)
def test_retargeting_lakebridge_editor_overlap_disallowed(inside_path: Path) -> None:
    """Verify that a retargeting editor cannot be created that writes into the base directory."""
    assert not inside_path.is_absolute()
    base = Path("/some") / "path"
    target = base / inside_path
    expected_message = f"Target directory may not be within the base directory {base}: {target}"
    with pytest.raises(ValueError, match=re.escape(expected_message)):
        LakebridgeEditor.retargeting_editor(base=base, target=target)


def test_retargeting_lakebridge_editor_rejects_outside_base(tmp_path: Path) -> None:
    """Verify that a retargeting editor will reject edits outside the base directory."""
    input = tmp_path / "input"
    output = tmp_path / "output"
    input.mkdir()
    output.mkdir()
    outside_file = tmp_path / "not_allowed.txt"

    # No need for extensive tests here, just checking for the presence of rejection.
    editor = LakebridgeEditor.retargeting_editor(base=input, target=output)
    edit = WorkspaceEdit(document_changes=(CreateFile(uri=outside_file.as_uri()),))
    result = editor.apply(edit)

    assert not result.applied
    assert result.failure_reason is not None and "must be within" in result.failure_reason
    assert not outside_file.exists()


def _conversion_output(p: Path, content: str) -> Sequence[DocumentChange]:
    return (_create_file(p), _text_replace(p, new_text=content))


def test_lakebridge_editor_transpile_sql(tmp_path: Path) -> None:
    """Verify the events needed for transpiling a SQL file, 1:1."""
    # Set up the paths for the scenario.
    input_path = tmp_path / "input"
    input_file = input_path / "queries" / "annual_report.sql"
    output_path = tmp_path / "output"
    output_file = output_path / "queries" / "annual_report.sql"

    # Prepare the content.
    input_file.parent.mkdir(parents=True)
    input_file.write_text("-- Input query, to be converted.\n", encoding="utf-8")

    # Apply the results of conversion.
    edit = WorkspaceEdit(document_changes=[*_conversion_output(input_file, content="-- Conversion output.\n")])
    editor = LakebridgeEditor.retargeting_editor(base=input_path, target=output_path)
    result = editor.apply(edit)

    # Verify the input was untouched and the output is as expected.
    assert result.applied
    assert input_file.read_text(encoding="utf-8")
    assert output_file.read_text(encoding="utf-8")


def test_lakebridge_editor_transpile_to_notebook(tmp_path: Path) -> None:
    """Verify the events needed for transpiling to a different type of file, such as SQL to notebook."""
    # Set up the paths for the scenario.
    input_path = tmp_path / "input"
    input_file = input_path / "ddl" / "stored_proc.sql"
    output_path = tmp_path / "output"
    output_file = output_path / "ddl" / "stored_proc.py"

    # Prepare the content.
    input_file.parent.mkdir(parents=True)
    input_file.write_text("-- File that could contain a stored procedure.\n", encoding="utf-8")

    # Apply the results of conversion.
    edit = WorkspaceEdit(
        document_changes=[*_conversion_output(input_file.with_suffix(".py"), content="# Python code.\n")]
    )
    editor = LakebridgeEditor.retargeting_editor(base=input_path, target=output_path)
    result = editor.apply(edit)

    # Verify the input was untouched and the output is as expected.
    assert result.applied
    assert input_file.read_text(encoding="utf-8")
    assert output_file.read_text(encoding="utf-8")


def test_lakebridge_editor_transpile_to_many(tmp_path: Path) -> None:
    """Verify the events needed for transpiling to multiple files, for example ETL to notebooks."""
    # Set up the paths for the scenario.
    input_path = tmp_path / "input"
    input_file = input_path / "schedule.xml"
    output_path = tmp_path / "output"
    output_files = (
        output_path / "schedule.json",
        output_path / "schedule_j1.py",
    )

    # Prepare some content.
    input_file.parent.mkdir(parents=True)
    input_file.write_text("<?xml version='1.0'?><etl/>\n", encoding="utf-8")

    # Apply the results of conversion.
    edit = WorkspaceEdit(
        document_changes=[
            *_conversion_output(input_file.with_suffix(".json"), content="{}\n"),
            *_conversion_output(input_path / "schedule_j1.py", content="# Python code.\n"),
        ]
    )
    editor = LakebridgeEditor.retargeting_editor(base=input_path, target=output_path)
    result = editor.apply(edit)

    # Verify the input was untouched and the output files all exist and have content.
    assert result.applied
    assert input_file.read_text(encoding="utf-8") == "<?xml version='1.0'?><etl/>\n"
    assert all(output_file.read_text(encoding="utf-8") for output_file in output_files)
