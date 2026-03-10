# WARNING: This file is excluded from inline-suppression enforcement (see push.yml).
# Test data strings intentionally contain suppression tag patterns.
# Do NOT add actual inline suppression comments (noqa, pylint: disable, ruff: noqa) to this file's code.
from tests.unit.no_cheat import no_cheat


# ===========================================================================
# Existing tests: # pylint: disable= behaviour
# ===========================================================================


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
-some other code # pylint: disable=some-rule
"""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_returns_empty_string_for_replaced_cheat():
    diff_data = """
+some code # pylint: disable=some-rule
-some other code # pylint: disable=some-rule
"""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_returns_message_for_single_cheat():
    diff_data = """
+some code # pylint: disable=some-rule
-some other code
"""
    result = no_cheat(diff_data)
    assert result == "Do not cheat the linter: found 1 additional # pylint: disable=some-rule"


def test_no_cheat_returns_message_for_multiple_same_cheat():
    diff_data = """
+some code # pylint: disable=some-rule
-some other code
+some code # pylint: disable=some-rule
"""
    result = no_cheat(diff_data)
    assert result == "Do not cheat the linter: found 2 additional # pylint: disable=some-rule"


def test_no_cheat_returns_message_for_multiple_cheats_in_different_lines():
    diff_data = """
+some code # pylint: disable=some-rule
-some other code
+some code # pylint: disable=some-other-rule
"""
    result = no_cheat(diff_data)
    assert set(result.split('\n')) == {
        "Do not cheat the linter: found 1 additional # pylint: disable=some-rule",
        "Do not cheat the linter: found 1 additional # pylint: disable=some-other-rule",
    }


def test_no_cheat_returns_message_for_multiple_cheats_in_same_lines():
    diff_data = """
+some code # pylint: disable=some-rule, some-other-rule
-some other code
"""
    result = no_cheat(diff_data)
    assert set(result.split('\n')) == {
        "Do not cheat the linter: found 1 additional # pylint: disable=some-rule",
        "Do not cheat the linter: found 1 additional # pylint: disable=some-other-rule",
    }


def test_no_cheat_returns_message_for_standalone_cyclic_import():
    diff_data = """
+some code # pylint: disable=cyclic-import
-some other code
"""
    result = no_cheat(diff_data)
    assert result == ("Do not cheat the linter: found 1 additional # pylint: disable=cyclic-import")


def test_no_cheat_returns_message_for_standalone_import_outside_toplevel():
    diff_data = """
+some code # pylint: disable=import-outside-toplevel
-some other code
"""
    result = no_cheat(diff_data)
    assert result == ("Do not cheat the linter: found 1 additional # pylint: disable=import-outside-toplevel")


def test_no_cheat_returns_empty_string_for_combined_cyclic_import_standalone_cyclic_import():
    diff_data = """
+some code # pylint: disable=cyclic-import, import-outside-toplevel
+some code # pylint: disable=import-outside-toplevel, cyclic-import
-some other code
"""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_returns_message_for_code_within_combined_cyclic_import_standalone_cyclic_import():
    diff_data = """
+some code # pylint: disable=some-rule, cyclic-import, import-outside-toplevel
+some code # pylint: disable=import-outside-toplevel, cyclic-import
-some other code
"""
    result = no_cheat(diff_data)
    assert result == ("Do not cheat the linter: found 1 additional # pylint: disable=some-rule")


# ===========================================================================
# New tests: # pylint: disable-next= behaviour
# ===========================================================================


def test_no_cheat_detects_disable_next():
    diff_data = """
+some code # pylint: disable-next=some-rule
-some other code
"""
    result = no_cheat(diff_data)
    assert result == "Do not cheat the linter: found 1 additional # pylint: disable-next=some-rule"


def test_no_cheat_allows_combined_cyclic_import_in_disable_next():
    diff_data = """
+some code # pylint: disable-next=cyclic-import, import-outside-toplevel
-some other code
"""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_rejects_partial_cyclic_in_disable_next():
    diff_data = """
+some code # pylint: disable-next=cyclic-import, bad-rule
-some other code
"""
    result = no_cheat(diff_data)
    assert "bad-rule" in result
    assert "cyclic-import" not in result


def test_no_cheat_returns_empty_for_removed_disable_next():
    diff_data = """
+some code
-some other code # pylint: disable-next=some-rule
"""
    result = no_cheat(diff_data)
    assert not result


# ===========================================================================
# New tests: # noqa inline suppression (bare, colon, space forms)
# ===========================================================================


def test_no_cheat_detects_bare_noqa():
    diff_data = """
+some code  # noqa
-some other code
"""
    result = no_cheat(diff_data)
    assert "# noqa" in result


def test_no_cheat_detects_noqa_colon_form():
    diff_data = """
+some code  # noqa: BLE001
-some other code
"""
    result = no_cheat(diff_data)
    assert "# noqa" in result


def test_no_cheat_detects_noqa_space_form():
    diff_data = """
+some code  # noqa BLE001
-some other code
"""
    result = no_cheat(diff_data)
    assert "# noqa" in result


def test_no_cheat_passes_on_noqa_net_removal():
    diff_data = """
+some code
-some other code  # noqa: BLE001
"""
    result = no_cheat(diff_data)
    assert not result


def test_no_cheat_passes_on_noqa_net_zero():
    diff_data = """
+some code  # noqa: BLE001
-some other code  # noqa: BLE001
"""
    result = no_cheat(diff_data)
    assert not result


# ===========================================================================
# New tests: # ruff: noqa file-level suppression
# ===========================================================================


def test_no_cheat_detects_ruff_noqa_with_code():
    diff_data = """
+# ruff: noqa: BLE001
-some removed line
"""
    result = no_cheat(diff_data)
    assert "# ruff: noqa" in result


def test_no_cheat_detects_bare_ruff_noqa():
    diff_data = """
+# ruff: noqa
-some removed line
"""
    result = no_cheat(diff_data)
    assert "# ruff: noqa" in result


def test_no_cheat_passes_on_ruff_noqa_net_zero():
    diff_data = """
+# ruff: noqa: BLE001
-# ruff: noqa: BLE001
"""
    result = no_cheat(diff_data)
    assert not result
