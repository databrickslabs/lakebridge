import codecs


# Optionally check to see if a string begins with a Byte Order Mark
# such a character will cause the transpiler to fail
def remove_bom(input_string: str) -> str:
    """
    Removes the Byte Order Mark (BOM) from the given string if it exists.
    :param input_string: String to remove BOM from
    :return: String without BOM
    """
    output_string = input_string

    # Check and remove UTF-16 (LE and BE) BOM
    if input_string.startswith(codecs.BOM_UTF16_BE.decode("utf-16-be")):
        output_string = input_string[len(codecs.BOM_UTF16_BE.decode("utf-16-be")) :]
    elif input_string.startswith(codecs.BOM_UTF16_LE.decode("utf-16-le")):
        output_string = input_string[len(codecs.BOM_UTF16_LE.decode("utf-16-le")) :]
    elif input_string.startswith(codecs.BOM_UTF16.decode("utf-16")):
        output_string = input_string[len(codecs.BOM_UTF16.decode("utf-16")) :]
    # Check and remove UTF-32 (LE and BE) BOM
    elif input_string.startswith(codecs.BOM_UTF32_BE.decode("utf-32-be")):
        output_string = input_string[len(codecs.BOM_UTF32_BE.decode("utf-32-be")) :]
    elif input_string.startswith(codecs.BOM_UTF32_LE.decode("utf-32-le")):
        output_string = input_string[len(codecs.BOM_UTF32_LE.decode("utf-32-le")) :]
    elif input_string.startswith(codecs.BOM_UTF32.decode("utf-32")):
        output_string = input_string[len(codecs.BOM_UTF32.decode("utf-32")) :]
    # Check and remove UTF-8 BOM
    elif input_string.startswith(codecs.BOM_UTF8.decode("utf-8")):
        output_string = input_string[len(codecs.BOM_UTF8.decode("utf-8")) :]

    return output_string


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
