-- Duplicate if the task_id is already stored or queued.
SELECT
  EXISTS (SELECT 1 FROM public.sast_all_raw_data_survey WHERE task_id = $1) AS in_raw,
  EXISTS (SELECT 1 FROM public.failed_scan_queue        WHERE task_id = $1) AS in_queue;
