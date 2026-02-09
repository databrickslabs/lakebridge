import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar, IO, TypeAlias

import attrs
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

Changes: TypeAlias = Mapping[str, Sequence[TextEdit]]
DocumentChange: TypeAlias = CreateFile | DeleteFile | RenameFile | TextDocumentEdit


logger = logging.getLogger(__name__)


class Editor(ABC):
    @abstractmethod
    def capabilities(self) -> WorkspaceEditClientCapabilities:
        """Return the capabilities of this editor."""

    @abstractmethod
    def apply(self, edit: WorkspaceEdit) -> ApplyWorkspaceEditResult:
        """Apply the given set of edits."""

    @classmethod
    def uri_as_path(cls, uri: str) -> Path | None:
        """Convert a URI to a filesystem path, if possible."""
        fs_path = to_fs_path(uri)
        if fs_path is None:
            return None
        return Path(fs_path).resolve(strict=False)


class BaseEditor(Editor):
    """A base editor implementation that sets up the plumbing for applying text edits."""

    def capabilities(self) -> WorkspaceEditClientCapabilities:
        return WorkspaceEditClientCapabilities(
            document_changes=True,
            resource_operations=list(self.supported_resource_operations()),
            failure_handling=self.failure_handling(),
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

    def _apply_changes(self, changes: Changes) -> ApplyWorkspaceEditResult:
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

    @classmethod
    def retargeting_editor(cls, base: Path, target: Path, *, write_buffering: int = -1) -> Editor:
        """Create an editor that will retarget changes from a given base and apply them into the target directory.

        Edits to files within the base directory will be retargeted relative to the target directory. Edits that refer
        to resources outside the base directory are not allowed and will cause the edit to be fail.

        Args:
              base: the base directory within edits are expected. This directory will be left alone.
              target: the target directory into which edits will be applied.
              write_buffering: The buffering mode to use when writing to files, passed to the `open()` function.
        Returns:
              an editor that will retarget applied edits.
        Raises:
              ValueError: if the target directory is within the base directory.
        """
        if target.resolve().is_relative_to(base.resolve()):
            msg = f"Target directory may not be within the base directory {base}: {target}"
            raise ValueError(msg)
        underlying_editor = LakebridgeEditor(write_buffering=write_buffering)
        retargeting_editor = RetargetingEditor(underlying_editor, base=base, target=target)
        sandbox_editor = SandboxEditor(retargeting_editor, base=base)
        return sandbox_editor

    def _apply_text_edits(self, uri: str, text_edits: Sequence[TextEdit]) -> ApplyWorkspaceEditResult:
        reason = f"Text edits are not supported, use document changes instead: {uri}"
        return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)

    def _create_file(self, edit: CreateFile) -> ApplyWorkspaceEditResult:
        # Determine the canonical path to the file that will be created. (Parts of the path may or may not exist.)
        path = self.uri_as_path(edit.uri)
        if path is None:
            reason = f"Cannot create file, invalid filesystem path: {edit.uri}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)

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
            logger.warning(
                f"Cannot create/truncate file, parent directory could not be created: {edit.uri}", exc_info=e
            )
            reason = f"Cannot create/truncate file ({edit.uri}), parent directory could not be created: {e}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)

        # Attempt to open the file for writing.
        buffering = self._write_buffering
        try:
            file = open(path, open_mode, encoding="utf-8", buffering=buffering)  # pylint: disable=consider-using-with
        except FileExistsError as e:
            if options and options.ignore_if_exists:
                return ApplyWorkspaceEditResult(applied=True)
            msg = f"Cannot create file, already exists: {edit.uri}"
            logger.warning(msg, exc_info=e)
            return ApplyWorkspaceEditResult(applied=False, failure_reason=msg)
        except OSError as e:
            logger.warning(f"Cannot create/truncate file: {edit.uri}", exc_info=e)
            msg = f"Cannot create/truncate file ({edit.uri}) due to error: {e}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=msg)

        # Store the open (and empty) file, so a subsequent edit can insert the content.
        self._open_files[path] = file
        return ApplyWorkspaceEditResult(applied=True)

    def _apply_document_edit(self, edit: TextDocumentEdit) -> ApplyWorkspaceEditResult:
        # Determine the canonical path to the file that is being replaced.
        path = self.uri_as_path(edit.text_document.uri)
        if path is None:
            reason = f"Cannot edit file, invalid filesystem path: {edit.text_document.uri}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)

        # We must already have an open file ready for the content. It's empty.
        try:
            with self._open_files.pop(path) as open_file:
                match edit.edits:
                    case [TextEdit(range=self._LSP_ORIGIN) as only_edit]:
                        normalized_text = self.normalize_line_endings(only_edit.new_text)
                        open_file.write(normalized_text)
                    case _:
                        reason = f"Unsupported document edit(s) for {edit.text_document.uri}, only a single insert at the start of the file is supported: {edit.edits}"
                        return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
        except KeyError:
            reason = f"Cannot modify a text document that is not newly created: {edit.text_document.uri}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
        except OSError as e:
            logger.warning(f"Cannot modify file due to error: {edit.text_document.uri}", exc_info=e)
            reason = f"Cannot modify file ({edit.text_document.uri}) due to error: {e}"
            return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)
        return ApplyWorkspaceEditResult(applied=True)

    def _rename_file(self, edit: RenameFile) -> ApplyWorkspaceEditResult:
        reason = f"Renaming files is not supported: {edit.old_uri} -> {edit.new_uri}"
        return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)

    def _delete_file(self, edit: DeleteFile) -> ApplyWorkspaceEditResult:
        reason = f"Deleting files is not supported: {edit.uri}"
        return ApplyWorkspaceEditResult(applied=False, failure_reason=reason)


class EditorProxy(Editor, ABC):
    """A base class for an editing proxy.

    This can examine (and potentially modify) edits before passing them to an underlying editor.
    """

    _editor: Editor
    """Underlying editor that will apply edits to the retargeted path."""

    def __init__(self, editor: Editor) -> None:
        self._editor = editor

    def capabilities(self) -> WorkspaceEditClientCapabilities:
        return self._editor.capabilities()

    def apply(self, edit: WorkspaceEdit) -> ApplyWorkspaceEditResult:
        match self._map_changes(edit.changes):
            case ApplyWorkspaceEditResult() as early_result:
                return early_result
            case mapped_changes:
                pass
        match self._map_document_changes(edit.document_changes):
            case ApplyWorkspaceEditResult() as early_result:
                return early_result
            case mapped_document_changes:
                pass
        updated_edit = attrs.evolve(edit, changes=mapped_changes, document_changes=mapped_document_changes)
        return self._editor.apply(updated_edit)

    def _map_changes(self, changes: Changes | None) -> Changes | None | ApplyWorkspaceEditResult:
        if not changes:
            return changes
        mapped_changes: dict[str, Sequence[TextEdit]] = {}
        for index, (uri, text_edits) in enumerate(changes.items()):
            try:
                uri, text_edits = self._map_text_edits(uri, text_edits)
            except ValueError as e:
                return ApplyWorkspaceEditResult(applied=False, failure_reason=str(e), failed_change=index)
            mapped_changes[uri] = text_edits
        return mapped_changes

    def _map_text_edits(self, uri: str, text_edits: Sequence[TextEdit]) -> tuple[str, Sequence[TextEdit]]:
        """Allow subclasses to modify (or reject) edits for a document.

        The default implementation returns the uri and edits as-is.

        Args:
              uri: the URI of the document to which the edits apply.
              text_edits: the edits to apply to the document.
        Returns:
              A tuple containing the updated URI and text edits to apply.
        Raises:
              ValueError: if the edits should not be applied. The underlying workspace-edit will fail using the
                string-value of the exception as the failure reason.
        """
        return uri, text_edits

    def _map_document_changes(
        self, document_changes: Sequence[DocumentChange] | None
    ) -> Sequence[DocumentChange] | None | ApplyWorkspaceEditResult:
        if not document_changes:
            return document_changes
        mapped_document_changes: list[DocumentChange] = []
        for index, document_change in enumerate(document_changes):
            try:
                mapped_document_change = self._map_document_change(document_change)
            except ValueError as e:
                return ApplyWorkspaceEditResult(applied=False, failure_reason=str(e), failed_change=index)
            mapped_document_changes.append(mapped_document_change)
        return mapped_document_changes

    def _map_document_change(self, document_change: DocumentChange) -> DocumentChange:
        """Allow subclasses to modify (or reject) a change to a document.

        The default implementation returns the change as-is.

        Args:
              document_change: the change to examine and/or modify.
        Returns:
              the change that should be applied.
        Raises:
              ValueError: if the change should not be applied. The underlying workspace-edit will fail using the
                string-value of the exception as the failure reason.
        """
        return document_change


class SandboxEditor(EditorProxy):
    """An editor that only allows edits within a specific directory."""

    _base: Path
    """The base path within which edits are allowed."""

    def __init__(self, editor: Editor, *, base: Path) -> None:
        super().__init__(editor)
        self._base = base

    def _map_text_edits(self, uri: str, text_edits: Sequence[TextEdit]) -> tuple[str, Sequence[TextEdit]]:
        uri, text_edits = super()._map_text_edits(uri, text_edits)
        path = self.uri_as_path(uri)
        if path is None:
            raise ValueError(f"Invalid uri for edits: {uri}")
        if not path.is_relative_to(self._base):
            raise ValueError(f"Edit not allowed, must be within {self._base}: {uri}")
        return uri, text_edits

    def _check_uri(self, uri: str, document_change: DocumentChange) -> None:
        path = self.uri_as_path(uri)
        if path is None:
            raise ValueError(f"Invalid uri for change: {document_change}")
        if not path.is_relative_to(self._base):
            raise ValueError(f"Invalid uri for change, must be within {self._base}: {document_change}")

    def _map_document_change(self, document_change: DocumentChange) -> DocumentChange:
        document_change = super()._map_document_change(document_change)
        match document_change:
            case CreateFile(uri=uri) | DeleteFile(uri=uri):
                self._check_uri(uri, document_change)
            case RenameFile(old_uri=old_uri, new_uri=new_uri):
                self._check_uri(old_uri, document_change)
                self._check_uri(new_uri, document_change)
            case TextDocumentEdit(text_document=text_document):
                self._check_uri(text_document.uri, document_change)
            case _:
                raise ValueError(f"Unsupported document change type: {document_change}")
        return document_change


class RetargetingEditor(EditorProxy):
    """An editor that retargets paths and passes through them to a downstream editor."""

    _base: Path
    """The base path, within which edits will be retargeted."""

    _target: Path
    """The target path where edits will be retargeted."""

    def __init__(self, editor: Editor, *, base: Path, target: Path) -> None:
        super().__init__(editor)
        self._base = base
        self._target = target

    def _retarget(self, uri: str) -> str:
        path = self.uri_as_path(uri)
        if path is None:
            raise ValueError(f"Invalid uri for change: {uri}")
        try:
            relative_path = path.relative_to(self._base)
        except ValueError:
            return path.as_uri()
        retargeted = self._target / relative_path
        return retargeted.as_uri()

    def _map_text_edits(self, uri: str, text_edits: Sequence[TextEdit]) -> tuple[str, Sequence[TextEdit]]:
        uri, text_edits = super()._map_text_edits(uri, text_edits)
        retargeted_uri = self._retarget(uri)
        return retargeted_uri, text_edits

    def _map_document_change(self, document_change: DocumentChange) -> DocumentChange:
        document_change = super()._map_document_change(document_change)
        retargeted_change: DocumentChange
        match document_change:
            case CreateFile(uri=uri) | DeleteFile(uri=uri):
                retargeted_uri = self._retarget(uri)
                retargeted_change = attrs.evolve(document_change, uri=retargeted_uri)
            case RenameFile(old_uri=old_uri, new_uri=new_uri):
                retargeted_old_uri = self._retarget(old_uri)
                retargeted_new_uri = self._retarget(new_uri)
                retargeted_change = attrs.evolve(
                    document_change, old_uri=retargeted_old_uri, new_uri=retargeted_new_uri
                )
            case TextDocumentEdit(text_document=text_document):
                retargeted_uri = self._retarget(text_document.uri)
                retargeted_document = attrs.evolve(text_document, uri=retargeted_uri)
                retargeted_change = attrs.evolve(document_change, text_document=retargeted_document)
            case _:
                raise ValueError(f"Unsupported document change type: {document_change}")
        return retargeted_change
