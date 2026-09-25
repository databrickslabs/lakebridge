CREATE TABLE IF NOT EXISTS costs_pricing_config (
    region_detected VARCHAR,
    region_source VARCHAR,
    tier VARCHAR,
    tier_source VARCHAR,
    is_cloud BOOLEAN,
    note VARCHAR,
    cloud_service VARCHAR,
    actual_billed_cost VARCHAR
);
