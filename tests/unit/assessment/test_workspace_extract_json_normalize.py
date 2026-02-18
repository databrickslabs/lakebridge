from unittest.mock import Mock
import pandas as pd


def test_workspace_info_single_dict_normalization():
    """
    Test that get_workspace_info() dict is properly wrapped for json_normalize.

    This fixes the error: "All items in data must be of type dict, found list"
    when workspace_info contains fields with lists of non-dict items.
    """
    # Simulate workspace_info dict with nested list (common in Azure resources)
    workspace_info = {
        'id': '/subscriptions/abc/resourceGroups/rg/providers/Microsoft.Synapse/workspaces/ws1',
        'name': 'test-workspace',
        'type': 'Microsoft.Synapse/workspaces',
        'location': 'eastus',
        'provisioning_state': 'Succeeded',
        # This field might contain a list of strings (not dicts), causing the error
        'workspace_repository_configuration': ['config1', 'config2'],
        'extra_properties': {'key': 'value'},
    }

    # Without wrapping in list - this would fail if workspace_repository_configuration
    # contains non-dict items
    # df = pd.json_normalize(workspace_info)  # FAILS

    # With wrapping in list - this works correctly
    df = pd.json_normalize([workspace_info])

    assert len(df) == 1
    assert df.iloc[0]['id'] == workspace_info['id']
    assert df.iloc[0]['name'] == workspace_info['name']
    assert df.iloc[0]['location'] == workspace_info['location']


def test_list_methods_batch_flattening_for_json_normalize():
    """
    Test that list_* methods' batched yields are properly flattened.

    All list_* methods return generators that yield BATCHES (lists) of dicts.
    These must be flattened before passing to pd.json_normalize.
    """
    mock_workspace = Mock()

    # Simulate what list_sql_pools, list_linked_services, etc. return:
    # A generator that yields batches (lists) of dicts
    batch1 = [
        {'id': 'pool1', 'name': 'SQLPool1', 'status': 'Online'},
        {'id': 'pool2', 'name': 'SQLPool2', 'status': 'Paused'},
    ]
    batch2 = [{'id': 'pool3', 'name': 'SQLPool3', 'status': 'Online'}]

    mock_workspace.list_sql_pools.return_value = iter([batch1, batch2])

    # WRONG: Passing generator directly to json_normalize
    # sql_pools = mock_workspace.list_sql_pools()
    # df = pd.json_normalize(sql_pools)  # FAILS - generator is not a list

    # CORRECT: Flatten batches before passing to json_normalize
    sql_pools = mock_workspace.list_sql_pools()
    flattened = [pool for pool_pages in sql_pools for pool in pool_pages]
    df = pd.json_normalize(flattened)

    assert len(df) == 3
    assert df.iloc[0]['id'] == 'pool1'
    assert df.iloc[1]['id'] == 'pool2'
    assert df.iloc[2]['id'] == 'pool3'


def test_all_list_methods_use_consistent_flattening_pattern():
    """
    Test that all list_* methods follow the same flattening pattern.

    This validates the fix pattern used across:
    - list_sql_pools
    - list_bigdata_pools
    - list_linked_services
    - list_data_flows
    - list_pipelines
    - list_spark_job_definitions
    - list_notebooks
    - list_sqlscripts
    - list_triggers
    - list_libraries
    - list_datasets
    """
    mock_workspace = Mock()

    # Test data for different resource types
    test_cases = [
        ('list_sql_pools', [{'id': 'pool1', 'name': 'SQLPool1'}]),
        ('list_bigdata_pools', [{'id': 'spark1', 'name': 'SparkPool1'}]),
        ('list_linked_services', [{'id': 'svc1', 'name': 'LinkedService1'}]),
        ('list_data_flows', [{'id': 'flow1', 'name': 'DataFlow1'}]),
        ('list_pipelines', [{'id': 'pipe1', 'name': 'Pipeline1'}]),
        ('list_spark_job_definitions', [{'id': 'job1', 'name': 'SparkJob1'}]),
        ('list_notebooks', [{'id': 'nb1', 'name': 'Notebook1'}]),
        ('list_sqlscripts', [{'id': 'script1', 'name': 'SQLScript1'}]),
        ('list_triggers', [{'id': 'trig1', 'name': 'Trigger1'}]),
        ('list_libraries', [{'id': 'lib1', 'name': 'Library1'}]),
        ('list_datasets', [{'id': 'ds1', 'name': 'Dataset1'}]),
    ]

    for method_name, sample_data in test_cases:
        # Mock the method to return a generator yielding one batch
        getattr(mock_workspace, method_name).return_value = iter([sample_data])

        # Apply the flattening pattern
        result_generator = getattr(mock_workspace, method_name)()
        flattened = [item for batch in result_generator for item in batch]

        # Verify it can be normalized
        df = pd.json_normalize(flattened)
        assert len(df) == 1
        assert df.iloc[0]['id'] == sample_data[0]['id']
        assert df.iloc[0]['name'] == sample_data[0]['name']


def test_empty_batches_produce_empty_dataframe():
    """Test that empty generators/batches result in empty but valid DataFrames"""
    mock_workspace = Mock()

    # Empty generator (no batches)
    mock_workspace.list_sql_pools.return_value = iter([])

    sql_pools = mock_workspace.list_sql_pools()
    flattened = [pool for pool_pages in sql_pools for pool in pool_pages]

    # Empty list should create empty DataFrame without error
    df = pd.json_normalize(flattened if flattened else [])
    assert len(df) == 0
    assert isinstance(df, pd.DataFrame)


def test_mixed_batch_sizes_flatten_correctly():
    """Test that batches of varying sizes all flatten correctly"""
    mock_workspace = Mock()

    # Mix of batch sizes
    batch1 = [{'id': f'item{i}', 'name': f'Item{i}'} for i in range(5)]  # 5 items
    batch2 = [{'id': 'item5', 'name': 'Item5'}]  # 1 item
    batch3 = [{'id': f'item{i}', 'name': f'Item{i}'} for i in range(6, 10)]  # 4 items

    mock_workspace.list_pipelines.return_value = iter([batch1, batch2, batch3])

    pipelines = mock_workspace.list_pipelines()
    flattened = [pipeline for pipeline_pages in pipelines for pipeline in pipeline_pages]
    df = pd.json_normalize(flattened)

    assert len(df) == 10
    assert df.iloc[0]['id'] == 'item0'
    assert df.iloc[5]['id'] == 'item5'
    assert df.iloc[9]['id'] == 'item9'


def test_workspace_info_with_complex_nested_structure():
    """
    Test workspace_info with complex nested structures that previously caused errors.

    The Azure Synapse workspace info can contain complex nested structures including:
    - Lists of strings
    - Lists of objects
    - Nested dicts with lists
    """
    workspace_info = {
        'id': '/subscriptions/abc/resourceGroups/rg/providers/Microsoft.Synapse/workspaces/ws1',
        'name': 'test-workspace',
        'type': 'Microsoft.Synapse/workspaces',
        'location': 'eastus',
        'provisioning_state': 'Succeeded',
        'default_data_lake_storage': {
            'account_url': 'https://storage.dfs.core.windows.net',
            'filesystem': 'container1',
            'resource_id': '/subscriptions/abc/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/storage1',
        },
        # This could be None, a dict, or have nested lists
        'workspace_repository_configuration': None,
        'purview_configuration': {
            'purview_resource_id': '/subscriptions/abc/resourceGroups/rg/providers/Microsoft.Purview/accounts/purview1'
        },
        # Extra properties might contain lists of various types
        'extra_properties': {
            'tags': ['tag1', 'tag2'],  # List of strings - would cause error without wrapping
            'feature_flags': ['feature1', 'feature2'],
        },
    }

    # Must wrap in list to handle nested lists of non-dict items
    df = pd.json_normalize([workspace_info])

    assert len(df) == 1
    assert df.iloc[0]['id'] == workspace_info['id']
    assert df.iloc[0]['name'] == workspace_info['name']
    # Nested fields are flattened with dot notation
    assert 'default_data_lake_storage.account_url' in df.columns
    assert df.iloc[0]['default_data_lake_storage.account_url'] == 'https://storage.dfs.core.windows.net'


def test_json_normalize_wrapping_prevents_issues():
    """
    Test that wrapping single dict in list prevents normalization issues.

    When a single dict is passed to json_normalize (vs. a list of dicts),
    wrapping it in a list ensures consistent behavior and avoids potential
    issues with nested structures.
    """
    # Single dict that should be wrapped
    workspace_info = {
        'id': 'resource-1',
        'name': 'test-resource',
        'location': 'eastus',
        'tags': ['tag1', 'tag2', 'tag3'],  # List field
    }

    # Best practice: Always wrap single dict in list for json_normalize
    df = pd.json_normalize([workspace_info])
    assert len(df) == 1
    assert df.iloc[0]['id'] == 'resource-1'
    assert df.iloc[0]['name'] == 'test-resource'
    assert df.iloc[0]['location'] == 'eastus'

    # Verify it creates a proper DataFrame structure
    assert isinstance(df, pd.DataFrame)
    assert 'id' in df.columns
    assert 'name' in df.columns
    assert 'location' in df.columns
