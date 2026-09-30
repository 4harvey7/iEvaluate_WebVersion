-- One row: the original scan image id with data, or NULL.
WITH a AS (
  SELECT CASE WHEN btrim(COALESCE($1, '')) ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
              THEN btrim($1)::uuid END AS fid,
         NULLIF(btrim(COALESCE($2, '')), '') AS tid
),
cand AS (
  SELECT f.scan_image_id AS id,
         CASE WHEN f.id = a.fid THEN 0 ELSE 1 END AS pri,
         f.created_at AS ts
  FROM public.failed_scan_queue f
  CROSS JOIN a
  WHERE f.scan_image_id IS NOT NULL
    AND (f.id = a.fid OR (a.tid IS NOT NULL AND f.task_id = a.tid))
  UNION ALL
  SELECT s.id, 2 AS pri, s.created_at AS ts
  FROM public.scan_error_images s
  CROSS JOIN a
  WHERE a.tid IS NOT NULL
    AND s.task_id = a.tid
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
