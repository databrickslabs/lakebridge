import pytest

from databricks.labs.lakebridge.reconcile.config_generator.configure import NormalizedMatcher


def test_match_all_exact_lowercase():
    matcher = NormalizedMatcher()
    result = matcher.match_all(["EMP_ID", "NAME"], ["emp_id", "name", "salary"])
    assert result == {"EMP_ID": "emp_id", "NAME": "name"}


def test_match_all_loose_normalization():
    matcher = NormalizedMatcher()
    result = matcher.match_all(["emp-id"], ["emp_id"])
    assert result == {"emp-id": "emp_id"}


def test_match_all_singularization():
    matcher = NormalizedMatcher()
    result = matcher.match_all(["categories"], ["category"])
    assert result == {"categories": "category"}


@pytest.mark.parametrize(
    ("plural", "singular"),
    [
        # `-sses` plurals where the singular ends in `-ss`: must strip `es` only.
        ("addresses", "address"),
        ("classes", "class"),
        ("businesses", "business"),
        ("processes", "process"),
        # Plain `-s` plurals where the singular ends in `-se`: must strip just `s`,
        # not `es`. (Regression target: the old `ses` rule mangled these to `hous`, `cas`, etc.)
        ("houses", "house"),
        ("cases", "case"),
        ("phases", "phase"),
        ("releases", "release"),
        ("expenses", "expense"),
        ("warehouses", "warehouse"),
        ("databases", "database"),
    ],
)
def test_match_all_singularization_handles_ss_and_se_endings(plural: str, singular: str) -> None:
    """Plural and singular forms of `-ss`/`-se`-ending words must normalize to the same key.

    Bug fence around the prior `ses -> s` rule that both regressed `-se` plurals (houses -> hous)
    and stripped the singular `-ss` words via the catch-all `s` rule (address -> addres).
    """
    matcher = NormalizedMatcher()
    assert matcher.match_all([plural], [singular]) == {plural: singular}
    assert matcher.match_all([singular], [plural]) == {singular: plural}


@pytest.mark.parametrize("word", ["address", "class", "business", "process"])
def test_match_all_preserves_ss_singular_self_match(word: str) -> None:
    """A singular word ending in `-ss` must match itself — i.e., not get stripped to `addres`/`clas`."""
    matcher = NormalizedMatcher()
    assert matcher.match_all([word], [word]) == {word: word}


def test_match_all_no_match_returns_none():
    matcher = NormalizedMatcher()
    result = matcher.match_all(["xyz"], ["abc", "def"])
    assert result == {"xyz": None}


def test_match_all_ambiguous_match_skipped():
    matcher = NormalizedMatcher()
    result = matcher.match_all(["address"], ["addresses", "address"])
    assert result == {"address": "address"}


def test_match_all_consumes_candidates():
    matcher = NormalizedMatcher()
    result = matcher.match_all(["emp_id", "EMP_ID"], ["emp_id"])
    assert result == {"emp_id": "emp_id", "EMP_ID": None}
