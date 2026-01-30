/**
 * Retrieves metadata for all routines (stored procedures and functions) in the
 * specified database by querying INFORMATION_SCHEMA.ROUTINES. Returns routine
 * details along with a timestamp indicating when the data was extracted.
 */
SELECT created,
       data_type,
       is_deterministic,
       is_implicitly_invocable,
       is_null_call,
       is_user_defined_cast,
       last_altered,
       max_dynamic_result_sets,
       numeric_precision,
       numeric_precision_radix,
       numeric_scale,
       routine_body,
       routine_catalog,
       '[REDACTED]' AS ROUTINE_DEFINITION,
       routine_name,
       routine_schema,
       routine_type,
       schema_level_routine,
       specific_catalog,
       specific_name,
       specific_schema,
       sql_data_access
FROM   information_schema.routines;
