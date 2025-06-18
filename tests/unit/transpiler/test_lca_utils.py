from pathlib import Path
from unittest.mock import patch

from sqlglot import parse_one

from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks
from databricks.labs.lakebridge.transpiler.sqlglot.lca_utils import check_for_unsupported_lca


def test_query_with_no_unsupported_lca_usage():
    dialect = get_dialect("snowflake")
    sql = """
        SELECT
            t.col1,
            t.col2,
            t.col3 AS ca,
        FROM table1 t
    """
    filename = "test_file1.sql"

    error = check_for_unsupported_lca(dialect, sql, Path(filename))
    assert not error


def test_query_with_valid_alias_usage():
    dialect = get_dialect("snowflake")
    sql = """
        WITH web_v1 as (
        select
          ws_item_sk item_sk, d_date,
          sum(sum(ws_sales_price))
              over (partition by ws_item_sk order by d_date rows between unbounded preceding and current row) cume_sales
        from web_sales
            ,date_dim
        where ws_sold_date_sk=d_date_sk
          and d_month_seq between 1212 and 1212+11
          and ws_item_sk is not NULL
        group by ws_item_sk, d_date),
        store_v1 as (
        select
          ss_item_sk item_sk, d_date,
          sum(sum(ss_sales_price))
              over (
                partition by ss_item_sk order by d_date rows between unbounded preceding and current row
              ) cume_sales
        from store_sales
            ,date_dim
        where ss_sold_date_sk=d_date_sk
          and d_month_seq between 1212 and 1212+11
          and ss_item_sk is not NULL
        group by ss_item_sk, d_date)
         select  *
        from (select item_sk
             ,d_date
             ,web_sales
             ,store_sales
             ,max(web_sales)
                 over (
                    partition by item_sk order by d_date rows between unbounded preceding and current row
                 ) web_cumulative
             ,max(store_sales)
                 over (
                    partition by item_sk order by d_date rows between unbounded preceding and current row
                 ) store_cumulative
             from (select case when web.item_sk is not null then web.item_sk else store.item_sk end item_sk
                         ,case when web.d_date is not null then web.d_date else store.d_date end d_date
                         ,web.cume_sales web_sales
                         ,store.cume_sales store_sales
                   from web_v1 web full outer join store_v1 store on (web.item_sk = store.item_sk
                                                                  and web.d_date = store.d_date)
                  )x )y
        where web_cumulative > store_cumulative
        order by item_sk
                ,d_date
        limit 100;
    """
    filename = "test_file1.sql"

    error = check_for_unsupported_lca(dialect, sql, Path(filename))
    assert not error


def test_query_with_lca_in_where():
    dialect = get_dialect("snowflake")
    sql = """
        SELECT
            t.col1,
            t.col2,
            t.col3 AS ca,
        FROM table1 t
        WHERE ca in ('v1', 'v2')
    """
    filename = "test_file2.sql"

    error = check_for_unsupported_lca(dialect, sql, Path(filename))
    assert error


def test_query_with_lca_in_window():
    dialect = get_dialect("snowflake")
    sql = """
        SELECT
            t.col1,
            t.col2,
            t.col3 AS ca,
            ROW_NUMBER() OVER (PARTITION by ca ORDER BY t.col2 DESC) AS rn
        FROM table1 t
    """
    filename = "test_file3.sql"

    error = check_for_unsupported_lca(dialect, sql, Path(filename))
    assert error


def test_query_with_error():
    dialect = get_dialect("snowflake")
    sql = """
        SELECT
            t.col1
            t.col2,
            t.col3 AS ca,
        FROM table1 t
    """
    filename = "test_file4.sql"

    error = check_for_unsupported_lca(dialect, sql, Path(filename))
    assert not error


def test_query_with_same_alias_and_column_name():
    dialect = get_dialect("snowflake")
    sql = """
    select ca_zip
     from (
      SELECT
      substr(ca_zip,1,5) ca_zip,
      trim(name) as name,
      count(*) over( partition by ca_zip)
      FROM customer_address
      WHERE substr(ca_zip,1,5) IN ('89436', '30868'));
    """
    filename = "test_file5.sql"

    error = check_for_unsupported_lca(dialect, sql, Path(filename))
    assert not error


def test_fix_lca_with_valid_lca_usage(normalize_string):
    input_sql = """
        SELECT
            t.col1,
            t.col2,
            t.col3 AS ca
        FROM table1 t
    """
    expected_sql = """
        SELECT
            t.col1,
            t.col2,
            t.col3 AS ca
        FROM table1 AS t
    """
    ast = parse_one(input_sql)
    generated_sql = ast.sql(Databricks, pretty=False)
    assert normalize_string(generated_sql) == normalize_string(expected_sql)


def test_fix_lca_with_lca_in_where(normalize_string):
    input_sql = """
        SELECT column_a as customer_id
        FROM my_table
        WHERE customer_id = '123'
    """
    expected_sql = """
        SELECT column_a as customer_id
        FROM my_table
        WHERE column_a = '123'
    """
    ast = parse_one(input_sql)
    generated_sql = ast.sql(Databricks, pretty=False)
    assert normalize_string(generated_sql) == normalize_string(expected_sql)


def test_fix_lca_with_lca_in_window(normalize_string):
    input_sql = """
        SELECT
            t.col1,
            t.col2,
            t.col3 AS ca,
            ROW_NUMBER() OVER (PARTITION by ca ORDER BY t.col2 DESC) AS rn
        FROM table1 t
    """
    expected_sql = """
        SELECT
            t.col1,
            t.col2,
            t.col3 AS ca,
            ROW_NUMBER() OVER (PARTITION by t.col3 ORDER BY t.col2 DESC) AS rn
        FROM table1 AS t
    """
    ast = parse_one(input_sql)
    generated_sql = ast.sql(Databricks, pretty=False)
    assert normalize_string(generated_sql) == normalize_string(expected_sql)


def test_fix_lca_with_lca_in_subquery(normalize_string):
    input_sql = """
        SELECT column_a as cid
        FROM my_table
        WHERE cid in (select cid as customer_id from customer_table where customer_id = '123')
    """
    expected_sql = """
        SELECT column_a as cid
        FROM my_table
        WHERE column_a in (select cid as customer_id from customer_table where cid = '123')
    """
    ast = parse_one(input_sql)
    generated_sql = ast.sql(Databricks, pretty=False)
    assert normalize_string(generated_sql) == normalize_string(expected_sql)


def test_fix_lca_with_lca_in_derived_table(normalize_string):
    input_sql = """
        SELECT column_a as cid
        FROM (select column_x as column_a, column_y as y from my_table where y = '456')
        WHERE cid = '123'
    """
    expected_sql = """
        SELECT column_a as cid
        FROM (select column_x as column_a, column_y as y from my_table where column_y = '456')
        WHERE column_a = '123'
    """
    ast = parse_one(input_sql)
    generated_sql = ast.sql(Databricks, pretty=False)
    assert normalize_string(generated_sql) == normalize_string(expected_sql)


def test_fix_lca_with_lca_in_subquery_and_derived_table(normalize_string):
    input_sql = """
        SELECT column_a as cid
        FROM (select column_x as column_a, column_y as y from my_table where y = '456')
        WHERE cid in (select cid as customer_id from customer_table where customer_id = '123')
    """
    expected_sql = """
        SELECT column_a as cid
        FROM (select column_x as column_a, column_y as y from my_table where column_y = '456')
        WHERE column_a in (select cid as customer_id from customer_table where cid = '123')
    """
    ast = parse_one(input_sql)
    generated_sql = ast.sql(Databricks, pretty=False)
    assert normalize_string(generated_sql) == normalize_string(expected_sql)


def test_fix_lca_in_cte(normalize_string):
    input_sql = """
        WITH cte AS (SELECT column_a as customer_id
            FROM my_table
            WHERE customer_id = '123')
        SELECT * FROM cte
    """
    expected_sql = """
        WITH cte AS (SELECT column_a as customer_id
            FROM my_table
            WHERE column_a = '123')
        SELECT * FROM cte
    """
    ast = parse_one(input_sql)
    generated_sql = ast.sql(Databricks, pretty=False)
    assert normalize_string(generated_sql) == normalize_string(expected_sql)


def test_fix_nested_lca(normalize_string):
    input_sql = """
        SELECT
            b * c as new_b,
            a - new_b as ab_diff
        FROM my_table
        WHERE ab_diff >= 0
    """
    expected_sql = """
        SELECT
            b * c as new_b,
            a - new_b as ab_diff
        FROM my_table
        WHERE a - b * c >= 0
    """
    ast = parse_one(input_sql)
    generated_sql = ast.sql(Databricks, pretty=False)
    assert normalize_string(generated_sql) == normalize_string(expected_sql)


def test_fix_nested_lca_with_no_scope(normalize_string):
    # This test is to check if the code can handle the case where the scope is not available
    # In this case we will not fix the invalid LCA and return the original query
    input_sql = """
        SELECT
            b * c as new_b,
            a - new_b as ab_diff
        FROM my_table
        WHERE ab_diff >= 0
    """
    expected_sql = """
        SELECT
            b * c as new_b,
            a - new_b as ab_diff
        FROM my_table
        WHERE ab_diff >= 0
    """
    ast = parse_one(input_sql)
    with patch(
        'databricks.labs.lakebridge.transpiler.sqlglot.lca_utils.build_scope',
        return_value=None,
    ):
        generated_sql = ast.sql(Databricks, pretty=False)
    assert normalize_string(generated_sql) == normalize_string(expected_sql)
