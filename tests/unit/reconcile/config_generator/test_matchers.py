from databricks.labs.lakebridge.reconcile.config_generator.matchers import (
    NormalizedMatcher,
    run_strategy_chain,
)


def test_normalize_steps_progressive():
    assert NormalizedMatcher.normalize_steps("Emp_Name") == ["emp_name", "emp_name", "empname", "empname"]
    assert NormalizedMatcher.normalize_steps("emp-name") == ["emp-name", "emp_name", "empname", "empname"]
    assert NormalizedMatcher.normalize_steps("emp name") == ["emp name", "emp_name", "empname", "empname"]
    assert NormalizedMatcher.normalize_steps("Categories") == ["categories", "categories", "categories", "category"]
    assert NormalizedMatcher.normalize_steps("Addresses") == ["addresses", "addresses", "addresses", "address"]


def test_naive_singularize():
    assert NormalizedMatcher.naive_singularize("categories") == "category"
    assert NormalizedMatcher.naive_singularize("addresses") == "address"
    assert NormalizedMatcher.naive_singularize("employees") == "employee"
    assert NormalizedMatcher.naive_singularize("data") == "data"
    assert NormalizedMatcher.naive_singularize("info") == "info"


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


def test_run_strategy_chain_marks_unmatched_none():
    result = run_strategy_chain([NormalizedMatcher()], ["a", "b"], ["a"])
    assert result == {"a": "a", "b": None}


def test_run_strategy_chain_runs_strategies_in_order():
    class FixedMatcher:
        def __init__(self, mapping: dict[str, str]):
            self._mapping = mapping

        def match_all(self, source_names, _):
            return {src: self._mapping.get(src) for src in source_names}

    first = FixedMatcher({"a": "x"})
    second = FixedMatcher({"b": "y"})

    result = run_strategy_chain([first, second], ["a", "b", "c"], ["x", "y", "z"])
    assert result == {"a": "x", "b": "y", "c": None}
