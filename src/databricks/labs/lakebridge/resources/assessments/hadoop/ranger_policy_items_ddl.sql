CREATE TABLE ranger_policy_items (
    policy_id BIGINT,
    service VARCHAR,
    policy_name VARCHAR,
    item_kind VARCHAR,
    users VARCHAR,
    groups VARCHAR,
    roles VARCHAR,
    accesses VARCHAR,
    delegate_admin BOOLEAN,
    conditions VARCHAR,
    mask_type VARCHAR,
    row_filter_expr VARCHAR
)
