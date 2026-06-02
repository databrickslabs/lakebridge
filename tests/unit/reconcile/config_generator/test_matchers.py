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
