import pytest

from databricks.labs.lakebridge.reconcile.constants import SamplingSpecificationsType
from databricks.labs.lakebridge.reconcile.recon_config import SamplingOptions, SamplingSpecifications


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


def test_sampling_specifications_coerces_string_type():
    spec = SamplingSpecifications(type="count", value=10)
    assert spec.type == SamplingSpecificationsType.COUNT


def test_sampling_specifications_fraction_disabled():
    with pytest.raises(ValueError, match="'FRACTION' type is disabled"):
        SamplingSpecifications(type=SamplingSpecificationsType.FRACTION, value=0.5)


@pytest.mark.parametrize("bad_value", [None, 0, 1, 2, -0.1])
def test_sampling_specifications_fraction_rejects_out_of_range_value(bad_value):
    with pytest.raises(ValueError, match="Fraction value must be greater than"):
        SamplingSpecifications(type=SamplingSpecificationsType.FRACTION, value=bad_value)


def test_sampling_specifications_none_value_defaults_to_50():
    spec = SamplingSpecifications(value=None)
    assert spec.value == 50


@pytest.mark.parametrize("bad_value", [0, -1, -100])
def test_sampling_specifications_floors_non_positive_value(bad_value, caplog):
    with caplog.at_level("WARNING"):
        spec = SamplingSpecifications(value=bad_value)
    assert spec.value == 50
    assert any(f"value={bad_value} is not positive" in rec.message for rec in caplog.records)


@pytest.mark.parametrize("bad_value", [True, False, "abc", [1]])
def test_sampling_specifications_rejects_bool_and_non_numeric(bad_value):
    with pytest.raises(ValueError, match="value must be int"):
        SamplingSpecifications(value=bad_value)


def test_sampling_specifications_count_coerces_to_int():
    spec = SamplingSpecifications(type=SamplingSpecificationsType.COUNT, value=5.7)
    assert spec.value == 5
    assert isinstance(spec.value, int)


def test_table_get_max_sample_size_default_when_no_options(table_conf):
    assert table_conf().get_max_sample_size() == 50


def test_table_get_max_sample_size_from_sampling_options(table_conf):
    table = table_conf(
        sampling_options=SamplingOptions(
            specifications=SamplingSpecifications(type=SamplingSpecificationsType.COUNT, value=200)
        )
    )
    assert table.get_max_sample_size() == 200
