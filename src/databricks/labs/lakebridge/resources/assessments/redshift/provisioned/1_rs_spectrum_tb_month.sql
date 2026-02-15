-- 1
select 'rs_spectrum_tb_month' as set_name,
       round(1.0 * sum(s3_scanned_bytes)/(1024*1024*1024*1024), 4) as s3_scanned_tb_month,
       round(s3_scanned_tb_month/30, 4) as avg_daily_scanned_tb,
       round(s3_scanned_tb_month/(30.0/7), 4) as avg_weekly_scanned_tb
  from svl_s3query_summary 
 where starttime >= current_date-30
;
