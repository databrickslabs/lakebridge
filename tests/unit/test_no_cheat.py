from tests.unit.no_cheat import no_cheat


def test_no_cheat_returns_empty_string_for_empty_diff():
    diff_data = ""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_returns_empty_string_for_no_cheat_diff():
    diff_data = """
+some code
-some other code
"""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_returns_empty_string_for_removed_cheat():
    diff_data = """
+some code
-some other code # ruff: noqa: some-rule
"""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_returns_empty_string_for_replaced_cheat():
    diff_data = """
+some code # ruff: noqa: some-rule
-some other code # ruff: noqa: some-rule
"""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_returns_message_for_single_cheat():
    diff_data = """
+some code # ruff: noqa: some-rule
-some other code
"""
    result = no_cheat(diff_data)
    assert result == "Do not cheat the linter: found 1 additional # ruff: noqa: some-rule"


def test_no_cheat_returns_message_for_multiple_same_cheat():
    diff_data = """
+some code # ruff: noqa: some-rule
-some other code
+some code # ruff: noqa: some-rule
"""
    result = no_cheat(diff_data)
    assert result == "Do not cheat the linter: found 2 additional # ruff: noqa: some-rule"


def test_no_cheat_returns_message_for_multiple_cheats_in_different_lines():
    diff_data = """
+some code # ruff: noqa: some-rule
-some other code
+some code # ruff: noqa: some-other-rule
"""
    result = no_cheat(diff_data)
    assert set(result.split('\n')) == {
        "Do not cheat the linter: found 1 additional # ruff: noqa: some-rule",
        "Do not cheat the linter: found 1 additional # ruff: noqa: some-other-rule",
    }


def test_no_cheat_returns_message_for_multiple_cheats_in_same_lines():
    diff_data = """
+some code # ruff: noqa: some-rule, some-other-rule
-some other code
"""
    result = no_cheat(diff_data)
    assert set(result.split('\n')) == {
        "Do not cheat the linter: found 1 additional # ruff: noqa: some-rule",
        "Do not cheat the linter: found 1 additional # ruff: noqa: some-other-rule",
    }


def test_no_cheat_returns_message_for_standalone_cyclic_import():
    diff_data = """
+some code # ruff: noqa: cyclic-import
-some other code
"""
    result = no_cheat(diff_data)
    assert result == ("Do not cheat the linter: found 1 additional # ruff: noqa: cyclic-import")


def test_no_cheat_returns_message_for_standalone_import_outside_toplevel():
    diff_data = """
+some code # ruff: noqa: import-outside-toplevel
-some other code
"""
    result = no_cheat(diff_data)
    assert result == ("Do not cheat the linter: found 1 additional # ruff: noqa: import-outside-toplevel")


def test_no_cheat_returns_empty_string_for_combined_cyclic_import_standalone_cyclic_import():
    diff_data = """
+some code # ruff: noqa: cyclic-import, import-outside-toplevel
+some code # ruff: noqa: import-outside-toplevel, cyclic-import
-some other code
"""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_returns_message_for_code_within_combined_cyclic_import_standalone_cyclic_import():
    diff_data = """
+some code # ruff: noqa: some-rule, cyclic-import, import-outside-toplevel
+some code # ruff: noqa: import-outside-toplevel, cyclic-import
-some other code
"""
    result = no_cheat(diff_data)
    assert result == ("Do not cheat the linter: found 1 additional # ruff: noqa: some-rule")
