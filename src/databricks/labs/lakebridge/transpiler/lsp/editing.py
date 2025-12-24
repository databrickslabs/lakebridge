import logging
import os.path
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, ClassVar

from pygls.uris import to_fs_path

from lsprotocol.types import (
    ApplyWorkspaceEditResult,
    CreateFile,
    DeleteFile,
    FailureHandlingKind,
    Position,
    Range,
    RenameFile,
    ResourceOperationKind,
    TextDocumentEdit,
    TextEdit,
    WorkspaceEdit,
    WorkspaceEditClientCapabilities,
)

DocumentChange = CreateFile | DeleteFile | RenameFile | TextDocumentEdit


logger = logging.getLogger(__name__)


class Editor(ABC):
    @classmethod
    @abstractmethod
    def capabilities(cls) -> WorkspaceEditClientCapabilities:
        """Return the capabilities of this editor."""

    @abstractmethod
    def apply(self, edit: WorkspaceEdit) -> ApplyWorkspaceEditResult:
        """Apply the given set of edits."""


class BaseEditor(Editor):
    """A base editor implementation that sets up the plumbing for applying text edits."""

    @classmethod
    def capabilities(cls) -> WorkspaceEditClientCapabilities:
        return WorkspaceEditClientCapabilities(
            document_changes=True,
            resource_operations=list(cls.supported_resource_operations()),
            failure_handling=cls.failure_handling(),
            normalizes_line_endings=True,
        )

    @classmethod
    def supported_resource_operations(cls) -> frozenset[ResourceOperationKind]:
        """The resource operations supported by this editor."""
        return frozenset()

    @classmethod
    def failure_handling(cls) -> FailureHandlingKind:
        """The failure handling method supported by this editor."""
        return FailureHandlingKind.Abort

    LINE_ENDINGS_TO_NORMALIZE = re.compile(r"\r\n?")

    @classmethod
    def normalize_line_endings(cls, text: str) -> str:
        """Normalize line endings.

        This means that:
          - \r\n will be converted to \n.
          - \r will be converted to \n.
          - \n will be left as-is.
        """
        return cls.LINE_ENDINGS_TO_NORMALIZE.sub("\n", text)

    def apply(self, edit: WorkspaceEdit) -> ApplyWorkspaceEditResult:
        """Apply the set of transformations."""
        # If document changes are present, these are applied in preference to changes
        if (document_changes := edit.document_changes) is not None:
            logger.debug(f"Applying workspace edit with {len(document_changes)} document changes.")
            result = self._apply_document_changes(document_changes)
        elif (changes := edit.changes) is not None:
            logger.debug(f"Applying workspace edit with {len(changes)} changes.")
            result = self._apply_changes(changes)
        else:
            # No changes to apply? Trivial success.
            logger.debug("Trivial workspace edit contains no changes.")
            result = ApplyWorkspaceEditResult(applied=True)
        if result.applied:
            logger.debug("Successfully applied entire workspace edit.")
        else:
            logger.debug(f"Could not (completely) apply workspace edit (result={result}): {edit}")
        return result

    def _apply_changes(self, changes: Mapping[str, Sequence[TextEdit]]) -> ApplyWorkspaceEditResult:
        for index, (uri, text_edits) in enumerate(changes.items()):
            if not (result := self._apply_text_edits(uri, text_edits)).applied:
                return ApplyWorkspaceEditResult(
                    applied=False, failure_reason=result.failure_reason, failed_change=index
                )
        return ApplyWorkspaceEditResult(applied=True)

    @abstractmethod
    def _apply_text_edits(self, uri: str, text_edits: Sequence[TextEdit]) -> ApplyWorkspaceEditResult:
        """Apply a sequence of edits to a specified file."""

    def _apply_document_changes(self, document_changes: Sequence[DocumentChange]) -> ApplyWorkspaceEditResult:
        for index, change in enumerate(document_changes):
            match change:
                case TextDocumentEdit():
                    result = self._apply_document_edit(change)
                case CreateFile():
                    result = self._create_file(change)
                case RenameFile():
                    result = self._rename_file(change)
                case DeleteFile():
                    result = self._delete_file(change)
                case _:
                    reason = f"Unsupported document change: {change}"
                    result = ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
            if not result.applied:
                return ApplyWorkspaceEditResult(
                    applied=False, failure_reason=result.failure_reason, failed_change=index
                )
        return ApplyWorkspaceEditResult(applied=True)

    @abstractmethod
    def _apply_document_edit(self, edit: TextDocumentEdit) -> ApplyWorkspaceEditResult: ...

    @abstractmethod
    def _create_file(self, edit: CreateFile) -> ApplyWorkspaceEditResult: ...

    @abstractmethod
    def _rename_file(self, edit: RenameFile) -> ApplyWorkspaceEditResult: ...

    @abstractmethod
    def _delete_file(self, edit: DeleteFile) -> ApplyWorkspaceEditResult: ...


class LakebridgeEditor(BaseEditor):
    """A limited editor that can handle replace files, but that's about it."""

    # Some details here. The intent is that:
    #  - Replacement uses a Create/Edit sequence:
    #      1. CreateFile(overwrite=true) to logically truncate. File is opened, to ensure errors are associated with
    #         the correct change event.
    #      2. Edit(start=end=0,0) to insert all the content (and flush).
    #  - Renaming is not supported.

    _LSP_ORIGIN: ClassVar[Range] = Range(start=Position(0, 0), end=Position(0, 0))

    _open_files: dict[Path, IO[str]]
    """Open files that have been created (if necessary) and are empty awaiting an edit to insert their content."""

    _write_buffering: int
    """The buffering argument to use for open() when writing to a file."""

    @classmethod
    def supported_resource_operations(cls) -> frozenset[ResourceOperationKind]:
        return frozenset({ResourceOperationKind.Create})

    def __init__(self, *, write_buffering: int = -1) -> None:
        self._open_files = {}
        self._write_buffering = write_buffering

    def _apply_text_edits(self, uri: str, text_edits: Sequence[TextEdit]) -> ApplyWorkspaceEditResult:
        reason = f"Text edits are not supported, use document changes instead: {uri}"
        return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)

    def _create_file(self, edit: CreateFile) -> ApplyWorkspaceEditResult:
        # Determine the canonical path to the file that will be created. (Parts of the path may or may not exist.)
        uri = edit.uri
        fs_path = to_fs_path(uri)
        if fs_path is None:
            reason = f"Cannot create file, invalid filesystem path: {uri}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
        real_path = os.path.realpath(fs_path)
        path = Path(real_path)

        # There are really 3 different modes of operation, depending on the options.
        #  - overwrite: options.overwrite is true
        #  - exclusive create: options.overwrite is false and options.ignore_if_exists is false.
        #  - create if not exists: options.overwrite is false and options.ignore_if_exists is true.
        # (Of these, only the first two guarantee an empty file.)
        options = edit.options
        open_mode = "w" if options and options.overwrite else "x"

        # Ensure the parent directory of the path exists.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"Cannot create/truncate file, parent directory could not be created: {uri}", exc_info=e)
            reason = f"Cannot create/truncate file ({uri}), parent directory could not be created: {e}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)

        # Attempt to open the file for writing.
        buffering = self._write_buffering
        try:
            file = open(path, open_mode, encoding="utf-8", buffering=buffering)  # pylint: disable=consider-using-with
        except FileExistsError as e:
            if options and options.ignore_if_exists:
                return ApplyWorkspaceEditResult(applied=True)
            msg = f"Cannot create file, already exists: {uri}"
            logger.warning(msg, exc_info=e)
            return ApplyWorkspaceEditResult(applied=False, failure_reason=msg)
        except OSError as e:
            logger.warning(f"Cannot create/truncate file: {uri}", exc_info=e)
            msg = f"Cannot create/truncate file ({uri}) due to error: {e}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=msg)

        # Store the open (and empty) file, so a subsequent edit can insert the content.
        self._open_files[path] = file
        return ApplyWorkspaceEditResult(applied=True)

    def _apply_document_edit(self, edit: TextDocumentEdit) -> ApplyWorkspaceEditResult:
        # Determine the canonical path to the file that is being replaced.
        uri = edit.text_document.uri
        fs_path = to_fs_path(uri)
        if fs_path is None:
            reason = f"Cannot edit file, invalid filesystem path: {uri}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
        real_path = os.path.realpath(fs_path)
        path = Path(real_path)

        # We must already have an open file ready for the content. It's empty.
        try:
            with self._open_files.pop(path) as open_file:
                match edit.edits:
                    case [TextEdit(range=self._LSP_ORIGIN) as only_edit]:
                        normalized_text = self.normalize_line_endings(only_edit.new_text)
                        open_file.write(normalized_text)
                    case _:
                        reason = f"Unsupported document edit(s) for {uri}, only a single insert at the start of the file is supported: {edit.edits}"
                        return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
        except KeyError:
            reason = f"Cannot modify a text document that is not newly created: {uri}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
        except OSError as e:
            logger.warning(f"Cannot modify file due to error: {uri}", exc_info=e)
            reason = f"Cannot modify file ({uri}) due to error: {e}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
        return ApplyWorkspaceEditResult(applied=True)

    def _rename_file(self, edit: RenameFile) -> ApplyWorkspaceEditResult:
        reason = f"Renaming files is not supported: {edit.old_uri} -> {edit.new_uri}"
        return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)

    def _delete_file(self, edit: DeleteFile) -> ApplyWorkspaceEditResult:
        reason = f"Deleting files is not supported: {edit.uri}"
        return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
