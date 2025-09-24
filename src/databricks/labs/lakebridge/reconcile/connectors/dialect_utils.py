from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier


class DialectUtils:
    _ANSI_IDENTIFIER_DELIMITER = "`"

    @staticmethod
    def unnormalize_identifier(identifier: str) -> str:
        """Return an ansi identifier without the outer backticks.

        Use this at your own risk as the missing outer backticks will result in bugs.
        E.g. <`mary's lamb`> is returned <mary's lamb> so the outer backticks are needed.
        This is useful for scenarios where the returned identifier will be part of another delimited identifier.

        :param identifier: a database identifier
        :return: ansi identifier without the outer backticks
        """
        ansi = DialectUtils.ansi_normalize_identifier(identifier)
        unescape = (
            DialectUtils._unescape_source_end_delimiter(ansi[1:-1], DialectUtils._ANSI_IDENTIFIER_DELIMITER)
            if ansi
            else ansi
        )
        return unescape

    @staticmethod
    def ansi_normalize_identifier(identifier: str) -> str:
        return DialectUtils.normalize_identifier(
            identifier, DialectUtils._ANSI_IDENTIFIER_DELIMITER, DialectUtils._ANSI_IDENTIFIER_DELIMITER
        ).ansi_normalized

    @staticmethod
    def normalize_identifier(
        identifier: str, source_start_delimiter: str, source_end_delimiter: str
    ) -> NormalizedIdentifier:
        identifier = identifier.strip().lower()

        ansi = DialectUtils._normalize_identifier_source_agnostic(
            identifier,
            source_start_delimiter,
            source_end_delimiter,
            DialectUtils._ANSI_IDENTIFIER_DELIMITER,
            DialectUtils._ANSI_IDENTIFIER_DELIMITER,
        )

        # Input was already ansi normalized
        if ansi == identifier:
            source = DialectUtils._normalize_identifier_source_agnostic(
                identifier,
                DialectUtils._ANSI_IDENTIFIER_DELIMITER,
                DialectUtils._ANSI_IDENTIFIER_DELIMITER,
                source_start_delimiter,
                source_end_delimiter,
            )

            # Ansi has backticks escaped which has to be unescaped for other delimiters and escape source end delimiters
            if source != ansi:
                source = DialectUtils._unescape_source_end_delimiter(source, DialectUtils._ANSI_IDENTIFIER_DELIMITER)
                source = (
                    DialectUtils._escape_source_end_delimiter(source, source_start_delimiter, source_end_delimiter)
                    if source
                    else source
                )
        else:
            # Make sure backticks are escaped properly for ansi and source end delimiters are unescaped
            ansi = DialectUtils._unescape_source_end_delimiter(ansi, source_end_delimiter)
            ansi = DialectUtils._escape_backticks(ansi) if ansi else ansi

            if source_end_delimiter != DialectUtils._ANSI_IDENTIFIER_DELIMITER:
                ansi = DialectUtils._unescape_source_end_delimiter(ansi, source_end_delimiter)

            source = DialectUtils._normalize_identifier_source_agnostic(
                identifier, source_start_delimiter, source_end_delimiter, source_start_delimiter, source_end_delimiter
            )

            # Make sure source end delimiter is escaped else nothing as it was already normalized
            if source != identifier:
                source = (
                    DialectUtils._escape_source_end_delimiter(source, source_start_delimiter, source_end_delimiter)
                    if source
                    else source
                )

        return NormalizedIdentifier(ansi, source)

    @staticmethod
    def _normalize_identifier_source_agnostic(
        identifier: str,
        source_start_delimiter: str,
        source_end_delimiter: str,
        expected_source_start_delimiter: str,
        expected_source_end_delimiter: str,
    ) -> str:
        if identifier == "" or identifier is None:
            return ""

        if DialectUtils.is_already_delimited(
            identifier, expected_source_start_delimiter, expected_source_end_delimiter
        ):
            return identifier

        if DialectUtils.is_already_delimited(identifier, source_start_delimiter, source_end_delimiter):
            stripped_identifier = identifier.removeprefix(source_start_delimiter).removesuffix(source_end_delimiter)
        else:
            stripped_identifier = identifier
        return f"{expected_source_start_delimiter}{stripped_identifier}{expected_source_end_delimiter}"

    @staticmethod
    def is_already_delimited(identifier: str, start_delimiter: str, end_delimiter: str) -> bool:
        return identifier.startswith(start_delimiter) and identifier.endswith(end_delimiter)

    @staticmethod
    def _escape_backticks(identifier: str) -> str:
        identifier = identifier[1:-1]
        identifier = identifier.replace("`", "``")
        return f"`{identifier}`"

    @staticmethod
    def _unescape_source_end_delimiter(identifier: str, source_end_delimiter: str) -> str:
        return identifier.replace(f"{source_end_delimiter}{source_end_delimiter}", source_end_delimiter)

    @staticmethod
    def _escape_source_end_delimiter(identifier: str, start_end_delimiter, source_end_delimiter: str) -> str:
        identifier = identifier[1:-1]
        identifier = identifier.replace(source_end_delimiter, f"{source_end_delimiter}{source_end_delimiter}")
        return f"{start_end_delimiter}{identifier}{source_end_delimiter}"
