CREATE TABLE IF NOT EXISTS costs_async_ingestion_by_table (
    database VARCHAR,
    "table" VARCHAR,
    async_inserted_bytes BIGINT,
    async_inserted_rows BIGINT,
    async_insert_batches BIGINT
);
