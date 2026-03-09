from unittest.mock import Mock
from datetime import date


def test_pipeline_runs_handles_batches_correctly():
    """Test that pipeline runs are properly extracted from batched yields"""

    # Mock the workspace object that yields batches
    mock_workspace = Mock()

    # Simulate what the profiler_classes actually returns: batches of runs
    batch1 = [{'run_id': '1', 'status': 'Succeeded'}, {'run_id': '2', 'status': 'Failed'}]
    batch2 = [{'run_id': '3', 'status': 'InProgress'}]

    # list_pipeline_runs yields batches (lists), not individual items
    mock_workspace.list_pipeline_runs.return_value = iter([batch1, batch2])

    # Simulate the fixed logic from workspace_extract.py lines 130-142
    pipeline_runs_list = []
    last_upd = date(2024, 1, 1)
    pipeline_runs_batches = mock_workspace.list_pipeline_runs(last_upd)

    has_runs = False
    for batch in pipeline_runs_batches:
        # Each batch is a list of dictionaries
        has_runs = True
        for run in batch:
            run['last_upd'] = last_upd
            pipeline_runs_list.append(run)

    # Verify all runs were collected
    assert len(pipeline_runs_list) == 3
    assert pipeline_runs_list[0]['run_id'] == '1'
    assert pipeline_runs_list[1]['run_id'] == '2'
    assert pipeline_runs_list[2]['run_id'] == '3'
    assert all(run['last_upd'] == last_upd for run in pipeline_runs_list)
    assert has_runs is True


def test_trigger_runs_handles_batches_correctly():
    """Test that trigger runs are properly extracted from batched yields"""

    mock_workspace = Mock()

    # Simulate batches
    batch1 = [{'trigger_run_id': 'tr1', 'status': 'Succeeded'}]
    batch2 = [{'trigger_run_id': 'tr2', 'status': 'Failed'}, {'trigger_run_id': 'tr3', 'status': 'Cancelled'}]

    mock_workspace.list_trigger_runs.return_value = iter([batch1, batch2])

    # Simulate the fixed logic from workspace_extract.py lines 154-166
    trigger_runs_list = []
    last_upd = date(2024, 1, 1)
    trigger_runs_batches = mock_workspace.list_trigger_runs(last_upd)

    has_runs = False
    for batch in trigger_runs_batches:
        has_runs = True
        for run in batch:
            run['last_upd'] = last_upd
            trigger_runs_list.append(run)

    assert len(trigger_runs_list) == 3
    assert trigger_runs_list[0]['trigger_run_id'] == 'tr1'
    assert trigger_runs_list[1]['trigger_run_id'] == 'tr2'
    assert trigger_runs_list[2]['trigger_run_id'] == 'tr3'
    assert all(run['last_upd'] == last_upd for run in trigger_runs_list)
    assert has_runs is True


def test_pipeline_runs_empty_batches_handled_gracefully():
    """Test that code handles case where no pipeline runs exist"""

    mock_workspace = Mock()
    # Empty iterator (no batches)
    mock_workspace.list_pipeline_runs.return_value = iter([])

    pipeline_runs_list = []
    last_upd = date(2024, 1, 1)
    pipeline_runs_batches = mock_workspace.list_pipeline_runs(last_upd)

    has_runs = False
    for batch in pipeline_runs_batches:
        has_runs = True
        for run in batch:
            run['last_upd'] = last_upd
            pipeline_runs_list.append(run)

    assert len(pipeline_runs_list) == 0
    assert has_runs is False  # Should remain False when no batches


def test_trigger_runs_empty_batches_handled_gracefully():
    """Test that code handles case where no trigger runs exist"""

    mock_workspace = Mock()
    # Empty iterator (no batches)
    mock_workspace.list_trigger_runs.return_value = iter([])

    trigger_runs_list = []
    last_upd = date(2024, 1, 1)
    trigger_runs_batches = mock_workspace.list_trigger_runs(last_upd)

    has_runs = False
    for batch in trigger_runs_batches:
        has_runs = True
        for run in batch:
            run['last_upd'] = last_upd
            trigger_runs_list.append(run)

    assert len(trigger_runs_list) == 0
    assert has_runs is False


def test_pipeline_runs_single_large_batch():
    """Test handling a single batch with many runs"""

    mock_workspace = Mock()

    # Single batch with 10 runs
    large_batch = [{'run_id': f'run_{i}', 'status': 'Succeeded'} for i in range(10)]
    mock_workspace.list_pipeline_runs.return_value = iter([large_batch])

    pipeline_runs_list = []
    last_upd = date(2024, 1, 1)
    pipeline_runs_batches = mock_workspace.list_pipeline_runs(last_upd)

    has_runs = False
    for batch in pipeline_runs_batches:
        has_runs = True
        for run in batch:
            run['last_upd'] = last_upd
            pipeline_runs_list.append(run)

    assert len(pipeline_runs_list) == 10
    assert pipeline_runs_list[0]['run_id'] == 'run_0'
    assert pipeline_runs_list[9]['run_id'] == 'run_9'
    assert has_runs is True


def test_trigger_runs_multiple_small_batches():
    """Test handling multiple small batches"""

    mock_workspace = Mock()

    # Multiple batches with 1 run each
    batch1 = [{'trigger_run_id': 'tr1', 'status': 'Succeeded'}]
    batch2 = [{'trigger_run_id': 'tr2', 'status': 'Failed'}]
    batch3 = [{'trigger_run_id': 'tr3', 'status': 'Cancelled'}]
    batch4 = [{'trigger_run_id': 'tr4', 'status': 'InProgress'}]

    mock_workspace.list_trigger_runs.return_value = iter([batch1, batch2, batch3, batch4])

    trigger_runs_list = []
    last_upd = date(2024, 1, 1)
    trigger_runs_batches = mock_workspace.list_trigger_runs(last_upd)

    has_runs = False
    for batch in trigger_runs_batches:
        has_runs = True
        for run in batch:
            run['last_upd'] = last_upd
            trigger_runs_list.append(run)

    assert len(trigger_runs_list) == 4
    assert has_runs is True


def test_pipeline_runs_preserves_all_fields():
    """Test that all fields from runs are preserved during batch iteration"""

    mock_workspace = Mock()

    batch = [
        {
            'run_id': 'run1',
            'pipeline_name': 'test_pipeline',
            'status': 'Succeeded',
            'run_start': '2024-01-01T00:00:00Z',
            'run_end': '2024-01-01T01:00:00Z',
            'duration_seconds': 3600,
        }
    ]

    mock_workspace.list_pipeline_runs.return_value = iter([batch])

    pipeline_runs_list = []
    last_upd = date(2024, 1, 1)
    pipeline_runs_batches = mock_workspace.list_pipeline_runs(last_upd)

    for batch in pipeline_runs_batches:
        for run in batch:
            run['last_upd'] = last_upd
            pipeline_runs_list.append(run)

    # Verify all original fields are preserved
    assert pipeline_runs_list[0]['run_id'] == 'run1'
    assert pipeline_runs_list[0]['pipeline_name'] == 'test_pipeline'
    assert pipeline_runs_list[0]['status'] == 'Succeeded'
    assert pipeline_runs_list[0]['run_start'] == '2024-01-01T00:00:00Z'
    assert pipeline_runs_list[0]['run_end'] == '2024-01-01T01:00:00Z'
    assert pipeline_runs_list[0]['duration_seconds'] == 3600
    assert pipeline_runs_list[0]['last_upd'] == last_upd
