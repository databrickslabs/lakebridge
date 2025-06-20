def refactor_hexadecimal_chars(input_string: str) -> str:
    """
    Updates the HexaDecimal characters ( \x1b[\\d+m ) in the given string as below.
    :param input_string: String with HexaDecimal characters. ex: ( \x1b[4mWHERE\x1b[0m )
    :return: String with HexaDecimal characters refactored to arrows. ex: ( --> WHERE <--)
    """
    output_string = input_string
    highlight = {"\x1b[4m": "--> ", "\x1b[0m": " <--"}
    for key, value in highlight.items():
        output_string = output_string.replace(key, value)
    return output_string


def format_error_message(error_type: str, error_message: Exception, error_sql: str) -> str:
    """
    Formats the error message with the error SQL.
    :param error_type: Error Type
    :param error_message: Error message
    :param error_sql: Error SQL
    :return: Formatted error message
    """
    error_str = (
        f"------------------------ {error_type} Start:------------------------\n"
        f"/*\n{str(error_message)}\n*/\n\n"
        f"/*\nOriginal Query:\n\n{str(error_sql)}\n*/\n"
        f"------------------------- {error_type} End:-------------------------"
    ).strip()
    return error_str
