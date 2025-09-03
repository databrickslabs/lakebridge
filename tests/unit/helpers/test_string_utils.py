from databricks.labs.lakebridge.helpers.string_utils import refactor_hexadecimal_chars, format_error_message


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
