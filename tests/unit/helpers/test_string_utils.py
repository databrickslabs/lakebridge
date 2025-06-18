import codecs

from databricks.labs.lakebridge.helpers.string_utils import remove_bom, refactor_hexadecimal_chars, format_error_message


def test_remove_bom():
    test_string = "test_string"

    # Test no BOM
    assert remove_bom(test_string) == test_string

    # Test UTF-16 BOM
    # "******** UTF-16 ********"
    input_string = codecs.BOM_UTF16.decode("utf-16") + test_string
    assert remove_bom(input_string) == test_string

    # "******** UTF-16-BE ********"
    input_string = codecs.BOM_UTF16_BE.decode("utf-16-be") + test_string
    assert remove_bom(input_string) == test_string

    # "******** UTF-16-LE ********"
    input_string = codecs.BOM_UTF16_LE.decode("utf-16-le") + test_string
    assert remove_bom(input_string) == test_string

    # Test UTF-32 BOM
    # "******** UTF-32 ********"
    input_string = codecs.BOM_UTF32.decode("utf-32") + test_string
    assert remove_bom(input_string) == test_string

    # "******** UTF-32-BE ********"
    input_string = codecs.BOM_UTF32_BE.decode("utf-32-be") + test_string
    assert remove_bom(input_string) == test_string

    # "******** UTF-32-LE ********"
    input_string = codecs.BOM_UTF32_LE.decode("utf-32-le") + test_string
    assert remove_bom(input_string) == test_string

    # Test UTF8 BOM
    # "******** UTF-8 ********"
    input_string = codecs.BOM_UTF8.decode("utf-8") + test_string
    assert remove_bom(input_string) == test_string


def test_refactor_hexadecimal_chars():
    input_string = "SELECT * FROM test \x1b[4mWHERE\x1b[0m"
    output_string = "SELECT * FROM test --> WHERE <--"
    assert refactor_hexadecimal_chars(input_string) == output_string

    input_string2 = "SELECT \x1b[4marray_agg(\x1b[0mafter_state order by timestamp asc) FROM dual"
    output_string2 = "SELECT --> array_agg( <--after_state order by timestamp asc) FROM dual"
    assert refactor_hexadecimal_chars(input_string2) == output_string2


def test_format_error_message():
    error_type = "PARSING ERROR"
    error_message = Exception("Syntax error near 'SELECT'")
    error_sql = "SELECT * FROM table"

    expected_output = (
        "------------------------ PARSING ERROR Start:------------------------\n"
        "/*\nSyntax error near 'SELECT'\n*/\n\n"
        "/*\nOriginal Query:\n\nSELECT * FROM table\n*/\n"
        "------------------------- PARSING ERROR End:-------------------------"
    )

    assert format_error_message(error_type, error_message, error_sql) == expected_output

    error_type = "TOKEN ERROR"
    error_message = Exception("Unexpected token 'FROM'")
    error_sql = "SELECT * FROM table"

    expected_output = (
        "------------------------ TOKEN ERROR Start:------------------------\n"
        "/*\nUnexpected token 'FROM'\n*/\n\n"
        "/*\nOriginal Query:\n\nSELECT * FROM table\n*/\n"
        "------------------------- TOKEN ERROR End:-------------------------"
    )

    assert format_error_message(error_type, error_message, error_sql) == expected_output
