from pathlib import Path
from typing import Sequence
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

from databricks.labs.lakebridge.transpiler.lsp.editing import BaseEditor, LakebridgeEditor


LSP_ORIGIN = Range(start=Position(0, 0), end=Position(0, 0))


def test_default_capabilities() -> None:
    """Verify the default set of capabilities that are reported."""
    capabilities = BaseEditor.capabilities()
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
    capabilities = LakebridgeEditor.capabilities()
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
        and result.failure_reason.startswith("Cannot edit a text document that is not newly created")
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
