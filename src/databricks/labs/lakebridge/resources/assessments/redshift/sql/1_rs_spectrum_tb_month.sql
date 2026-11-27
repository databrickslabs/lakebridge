-- 1
select 'rs_spectrum_tb_month' set_name 
      ,round(sum(returned_bytes)/(1024.0*1024*1024*1024),4)::double precision s3_scanned_tb_month
      ,round(sum(returned_bytes)/(1024.0*1024*1024*1024)/30,4)::double precision avg_daily_scanned_tb  -- 30-day window
  from sys_external_query_detail
where start_time >= current_date-30
  and source_type = 'S3'
group by 1
;
