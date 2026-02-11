-- 1
select 'rs_spectrum_tb_month' set_name 
      ,round(sum(returned_bytes)/(1024.0*1024*1024*1024),4) s3_scanned_tb_month
      ,round(avg(s3_scanned_tb_month) over (), 4 ) avg_daily_scanned_tb
  from sys_external_query_detail
where start_time >= current_date-30
  and source_type = 'S3'
group by 1
order by 1
;
