CREATE TABLE IF NOT EXISTS costs_async_ingestion_by_table (
    database VARCHAR,
    "table" VARCHAR,
    async_inserted_bytes UBIGINT,
    async_inserted_rows UBIGINT,
    async_insert_batches UBIGINT
);
