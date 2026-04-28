import pytest

from databricks.labs.lakebridge.reconcile.recon_config import InvalidMaxSampleSizeException


def test_table_without_join_column(table_conf):
    table_conf = table_conf()
    assert table_conf.get_join_columns("source") is None
    assert table_conf.get_drop_columns("source") == set()
    assert table_conf.get_partition_column("source") == set()
    assert table_conf.get_partition_column("target") == set()
    assert table_conf.get_filter("source") is None
    assert table_conf.get_filter("target") is None
    assert table_conf.get_threshold_columns("source") == set()


def test_table_with_all_options(table_conf_with_opts):
    ## layer == source

    assert table_conf_with_opts.get_join_columns("source") == {"s_nationkey", "s_suppkey"}
    assert table_conf_with_opts.get_drop_columns("source") == {"s_comment"}
    assert table_conf_with_opts.get_partition_column("source") == {"s_nationkey"}
    assert table_conf_with_opts.get_partition_column("target") == set()
    assert table_conf_with_opts.get_filter("source") == "s_name='t' and s_address='a'"
    assert table_conf_with_opts.get_threshold_columns("source") == {"s_acctbal"}

    ## layer == target
    assert table_conf_with_opts.get_join_columns("target") == {"s_nationkey_t", "s_suppkey_t"}
    assert table_conf_with_opts.get_drop_columns("target") == {"s_comment_t"}
    assert table_conf_with_opts.get_partition_column("target") == set()
    assert table_conf_with_opts.get_filter("target") == "s_name='t' and s_address_t='a'"
    assert table_conf_with_opts.get_threshold_columns("target") == {"s_acctbal_t"}


def test_table_without_column_mapping(table_conf):
    table_conf = table_conf()

    assert table_conf.get_tgt_to_src_col_mapping_list(["s_address", "s_name"]) == {"s_address", "s_name"}
    assert table_conf.get_layer_tgt_to_src_col_mapping("s_address_t", "target") == "s_address_t"
    assert table_conf.get_layer_tgt_to_src_col_mapping("s_address", "source") == "s_address"
    assert table_conf.get_src_to_tgt_col_mapping_list(["s_address", "s_name"], "source") == {"s_address", "s_name"}
    assert table_conf.get_src_to_tgt_col_mapping_list(["s_address", "s_name"], "target") == {"s_address", "s_name"}
    assert table_conf.get_layer_src_to_tgt_col_mapping("s_address", "source") == "s_address"


def test_max_sample_size_defaults_to_50_when_not_provided(table_conf):
    assert table_conf().get_max_sample_size() == 50


def test_max_sample_size_within_bounds_kept_as_is(table_conf):
    assert table_conf(max_sample_size=200).get_max_sample_size() == 200


def test_max_sample_size_at_boundaries_kept_as_is(table_conf):
    assert table_conf(max_sample_size=50).get_max_sample_size() == 50
    assert table_conf(max_sample_size=50_000).get_max_sample_size() == 50_000


@pytest.mark.parametrize("value", [49, 0, -1, -100])
def test_max_sample_size_below_min_is_floored(table_conf, value):
    assert table_conf(max_sample_size=value).get_max_sample_size() == 50


def test_max_sample_size_above_max_is_capped(table_conf):
    assert table_conf(max_sample_size=50_001).get_max_sample_size() == 50_000


@pytest.mark.parametrize("value", [True, False])
def test_max_sample_size_bool_raises(table_conf, value):
    with pytest.raises(InvalidMaxSampleSizeException):
        table_conf(max_sample_size=value)


@pytest.mark.parametrize("value", ["200", 50.5, 200.0, [50], None.__class__])
def test_max_sample_size_non_int_raises(table_conf, value):
    with pytest.raises(InvalidMaxSampleSizeException):
        table_conf(max_sample_size=value)
