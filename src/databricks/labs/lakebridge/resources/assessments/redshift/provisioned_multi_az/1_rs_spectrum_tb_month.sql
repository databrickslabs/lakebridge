-- 1
select 'rs_spectrum_tb_month' as set_name,
       round(1.0 * sum(returned_bytes)/(1024*1024*1024*1024), 4) as s3_scanned_tb_month,
       round(s3_scanned_tb_month/30, 4) as avg_daily_scanned_tb,
       round(s3_scanned_tb_month/(30.0/7), 4) as avg_weekly_scanned_tb
  from sys_external_query_detail 
 where source_type = 'S3'
   and start_time >= current_date-30
;
