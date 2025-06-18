import pytest
from pyspark.sql import Row

from databricks.labs.lakebridge.reconcile.constants import SamplingOptionMethod, SamplingSpecificationsType
from databricks.labs.lakebridge.reconcile.recon_config import (
    SamplingOptions,
    SamplingSpecifications,
)
from databricks.labs.lakebridge.reconcile.sampler import SamplerFactory

_MIN_SAMPLE_COUNT = 50
_MAX_SAMPLE_COUNT = 400


def test_random_sampler_count(mock_spark):
    keys_df = mock_spark.createDataFrame([Row(key=1), Row(key=2), Row(key=3), Row(key=4), Row(key=5)])
    keys_df_count = keys_df.count()

    target_table_df = mock_spark.createDataFrame(
        [
            Row(key=1, state="NC", age=25),
            Row(key=2, state="NC", age=30),
            Row(key=3, state="LA", age=35),
            Row(key=4, state="LA", age=40),
            Row(key=5, state="LA", age=45),
            Row(key=6, state="NY", age=50),
            Row(key=7, state="NY", age=55),
            Row(key=8, state="NY", age=65),
            Row(key=9, state="TX", age=70),
            Row(key=10, state="TX", age=75),
        ]
    )
    sample_count = 2.0
    random_sampling_options = SamplingOptions(
        method=SamplingOptionMethod.RANDOM,
        specifications=SamplingSpecifications(type=SamplingSpecificationsType.COUNT, value=sample_count),
        stratified_columns=None,
        stratified_buckets=None,
    )

    # Create RandomSampler instance
    random_sampler = SamplerFactory.get_sampler(random_sampling_options)

    # Perform random sampling
    random_sample = random_sampler.sample(keys_df, keys_df_count, ["key"], target_table_df)

    assert (
        random_sample.count() <= (sample_count if sample_count > _MIN_SAMPLE_COUNT else _MIN_SAMPLE_COUNT)
        if sample_count <= _MAX_SAMPLE_COUNT
        else _MAX_SAMPLE_COUNT
    )


def test_random_sampler_negative_count(mock_spark):
    keys_df = mock_spark.createDataFrame([Row(key=1), Row(key=2), Row(key=3), Row(key=4), Row(key=5)])
    keys_df_count = keys_df.count()

    target_table_df = mock_spark.createDataFrame(
        [
            Row(key=1, state="NC", age=25),
            Row(key=2, state="NC", age=30),
            Row(key=3, state="LA", age=35),
            Row(key=4, state="LA", age=40),
            Row(key=5, state="LA", age=45),
            Row(key=6, state="NY", age=50),
            Row(key=7, state="NY", age=55),
            Row(key=8, state="NY", age=65),
            Row(key=9, state="TX", age=70),
            Row(key=10, state="TX", age=75),
        ]
    )
    sample_count = -2.0
    random_sampling_options = SamplingOptions(
        method=SamplingOptionMethod.RANDOM,
        specifications=SamplingSpecifications(type=SamplingSpecificationsType.COUNT, value=sample_count),
        stratified_columns=None,
        stratified_buckets=None,
    )

    # Create RandomSampler instance
    random_sampler = SamplerFactory.get_sampler(random_sampling_options)

    # Perform random sampling
    random_sample = random_sampler.sample(keys_df, keys_df_count, ["key"], target_table_df)

    assert (
        random_sample.count() <= (sample_count if sample_count > _MIN_SAMPLE_COUNT else _MIN_SAMPLE_COUNT)
        if sample_count <= _MAX_SAMPLE_COUNT
        else _MAX_SAMPLE_COUNT
    )


def test_random_sampler_invalid_fraction():
    with pytest.raises(ValueError, match="SamplingSpecifications: 'FRACTION' type is disabled"):
        SamplingOptions(
            method=SamplingOptionMethod.RANDOM,
            specifications=SamplingSpecifications(type=SamplingSpecificationsType.FRACTION, value=-0.1),
            stratified_columns=None,
            stratified_buckets=None,
        )


def test_stratified_sampler_count(mock_spark):
    keys_df = mock_spark.createDataFrame([Row(key=1), Row(key=2), Row(key=3), Row(key=4), Row(key=5)])
    keys_df_count = keys_df.count()

    target_table_df = mock_spark.createDataFrame(
        [
            Row(key=1, state="NC", age=25),
            Row(key=2, state="NC", age=30),
            Row(key=3, state="LA", age=35),
            Row(key=4, state="LA", age=40),
            Row(key=5, state="LA", age=45),
            Row(key=6, state="NY", age=50),
            Row(key=7, state="NY", age=55),
            Row(key=8, state="NY", age=65),
            Row(key=9, state="TX", age=70),
            Row(key=10, state="TX", age=75),
        ]
    )
    sample_count = 2.0
    stratified_sampling_options = SamplingOptions(
        method=SamplingOptionMethod.STRATIFIED,
        specifications=SamplingSpecifications(type=SamplingSpecificationsType.COUNT, value=sample_count),
        stratified_columns=["state"],
        stratified_buckets=3,
    )

    # Create StratifiedSampler instance
    stratified_sampler = SamplerFactory.get_sampler(stratified_sampling_options)

    # Perform stratified sampling
    stratified_sample = stratified_sampler.sample(keys_df, keys_df_count, ["key"], target_table_df)

    assert (
        stratified_sample.count() <= (sample_count if sample_count > _MIN_SAMPLE_COUNT else _MIN_SAMPLE_COUNT)
        if sample_count <= _MAX_SAMPLE_COUNT
        else _MAX_SAMPLE_COUNT
    )


def test_stratified_sampler_missing_columns_buckets():
    with pytest.raises(
        ValueError,
        match="SamplingOptions : stratified_columns and stratified_buckets are required for STRATIFIED method",
    ):
        SamplingOptions(
            method=SamplingOptionMethod.STRATIFIED,
            specifications=SamplingSpecifications(type=SamplingSpecificationsType.COUNT, value=2.0),
            stratified_columns=["state"],
            stratified_buckets=None,
        )

    with pytest.raises(
        ValueError,
        match="SamplingOptions : stratified_columns and stratified_buckets are required for STRATIFIED method",
    ):
        SamplingOptions(
            method=SamplingOptionMethod.STRATIFIED,
            specifications=SamplingSpecifications(type=SamplingSpecificationsType.COUNT, value=2.0),
            stratified_columns=None,
            stratified_buckets=10,
        )

    with pytest.raises(
        ValueError,
        match="SamplingOptions : stratified_columns and stratified_buckets are required for STRATIFIED method",
    ):
        SamplingOptions(
            method=SamplingOptionMethod.STRATIFIED,
            specifications=SamplingSpecifications(type=SamplingSpecificationsType.COUNT, value=2.0),
            stratified_columns=None,
            stratified_buckets=None,
        )


def test_stratified_sampler_negative_count(mock_spark):
    keys_df = mock_spark.createDataFrame([Row(key=1), Row(key=2), Row(key=3), Row(key=4), Row(key=5)])
    keys_df_count = keys_df.count()

    target_table_df = mock_spark.createDataFrame(
        [
            Row(key=1, state="NC", age=25),
            Row(key=2, state="NC", age=30),
            Row(key=3, state="LA", age=35),
            Row(key=4, state="LA", age=40),
            Row(key=5, state="LA", age=45),
            Row(key=6, state="NY", age=50),
            Row(key=7, state="NY", age=55),
            Row(key=8, state="NY", age=65),
            Row(key=9, state="TX", age=70),
            Row(key=10, state="TX", age=75),
        ]
    )
    sample_count = -2.0
    stratified_sampling_options = SamplingOptions(
        method=SamplingOptionMethod.STRATIFIED,
        specifications=SamplingSpecifications(type=SamplingSpecificationsType.COUNT, value=sample_count),
        stratified_columns=["state"],
        stratified_buckets=3,
    )

    # Create StratifiedSampler instance
    stratified_sampler = SamplerFactory.get_sampler(stratified_sampling_options)

    # Perform stratified sampling
    stratified_sample = stratified_sampler.sample(keys_df, keys_df_count, ["key"], target_table_df)

    assert (
        stratified_sample.count() <= (sample_count if sample_count > _MIN_SAMPLE_COUNT else _MIN_SAMPLE_COUNT)
        if sample_count <= _MAX_SAMPLE_COUNT
        else _MAX_SAMPLE_COUNT
    )


def test_stratified_sampler_invalid_fraction():
    with pytest.raises(ValueError, match="SamplingSpecifications: 'FRACTION' type is disabled"):
        SamplingOptions(
            method=SamplingOptionMethod.STRATIFIED,
            specifications=SamplingSpecifications(type=SamplingSpecificationsType.FRACTION, value=-0.1),
            stratified_columns=["state"],
            stratified_buckets=3,
        )
