-- One row: the scan image id with data, or NULL for sheet rows.
WITH a AS (
  SELECT CASE WHEN btrim(COALESCE($1, '')) ~ '^[0-9]{1,18}$' THEN btrim($1)::bigint END AS eid,
         NULLIF(btrim(COALESCE($2, '')), '') AS tid
),
t AS (
  SELECT COALESCE(a.tid, (SELECT NULLIF(btrim(ie.task_id), '') FROM public.import_errors ie
                          WHERE ie.id = a.eid LIMIT 1)) AS tid,
         a.eid
  FROM a
),
cand AS (
  SELECT ie.scan_image_id AS id, 0 AS pri, ie.created_at AS ts
  FROM public.import_errors ie
  CROSS JOIN t
  WHERE ie.id = t.eid
    AND ie.scan_image_id IS NOT NULL
  UNION ALL
  SELECT f.scan_image_id, 1, f.created_at
  FROM public.failed_scan_queue f
  CROSS JOIN t
  WHERE t.tid IS NOT NULL
    AND f.task_id = t.tid
    AND f.scan_image_id IS NOT NULL
  UNION ALL
  SELECT s.id, 2, s.created_at
  FROM public.scan_error_images s
  CROSS JOIN t
  WHERE t.tid IS NOT NULL
    AND s.task_id = t.tid
),
q AS (
  SELECT c.id AS scan_image_id
  FROM cand c
  JOIN public.scan_error_images s ON s.id = c.id
  WHERE COALESCE(s.image_base64, '') <> ''
  ORDER BY c.pri, c.ts ASC
  LIMIT 1
)
SELECT scan_image_id FROM q
UNION ALL
SELECT NULL::uuid WHERE NOT EXISTS (SELECT 1 FROM q);
