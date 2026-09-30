-- Rebuilds the totals from all raw rows, not just this run.

-- global lock held until commit so recomputes don't race
DO $lock$ BEGIN PERFORM pg_advisory_xact_lock(hashtext('ievaluate:recompute_aggregates')); END $lock$;
WITH link AS (
  INSERT INTO public.instructor_subjects (subject_id, instructor_id, term_id)
  SELECT $3, $1, $2
  WHERE $1 IS NOT NULL AND $2 IS NOT NULL AND $3 IS NOT NULL
  ON CONFLICT (subject_id, instructor_id, term_id) DO NOTHING
  RETURNING 1 AS linked
),
agg AS (
  SELECT
    COUNT(*)::numeric AS n,
    COALESCE(SUM(r.m1), 0)::numeric AS m1,
    COALESCE(SUM(r.m2), 0)::numeric AS m2,
    COALESCE(SUM(r.m3), 0)::numeric AS m3,
    COALESCE(SUM(r.m4), 0)::numeric AS m4,
    COALESCE(SUM(r.m5), 0)::numeric AS m5,
    COALESCE(SUM(r.m6), 0)::numeric AS m6,
    COALESCE(SUM(r.m7), 0)::numeric AS m7,
    COALESCE(SUM(r.m8), 0)::numeric AS m8,
    COALESCE(SUM(r.m9), 0)::numeric AS m9,
    COALESCE(SUM(r.m10), 0)::numeric AS m10,
    COALESCE(SUM(r.p1), 0)::numeric AS p1,
    COALESCE(SUM(r.p2), 0)::numeric AS p2,
    COALESCE(SUM(r.p3), 0)::numeric AS p3,
    COALESCE(SUM(r.p4), 0)::numeric AS p4,
    COALESCE(SUM(r.p5), 0)::numeric AS p5,
    COALESCE(SUM(r.p6), 0)::numeric AS p6,
    COALESCE(SUM(r.p7), 0)::numeric AS p7,
    COALESCE(SUM(r.p8), 0)::numeric AS p8,
    COALESCE(SUM(r.p9), 0)::numeric AS p9,
    COALESCE(SUM(r.p10), 0)::numeric AS p10
  FROM public.sast_all_raw_data_survey r
  WHERE r."instructor_ID" = $1
    AND r.term_id         = $2
    AND r.subject_id      = $3
),
mgmt AS (
  INSERT INTO public.management_results (
    instructor_id, term_id, subject_id, total_responses,
    m1_total, m2_total, m3_total, m4_total, m5_total, m6_total, m7_total, m8_total, m9_total, m10_total, grand_total,
    m1_mean, m2_mean, m3_mean, m4_mean, m5_mean, m6_mean, m7_mean, m8_mean, m9_mean, m10_mean,
    overall_management_mean, average_per_response_management
  )
  SELECT $1, $2, $3, a.n,
         a.m1, a.m2, a.m3, a.m4, a.m5, a.m6, a.m7, a.m8, a.m9, a.m10,
         (a.m1 + a.m2 + a.m3 + a.m4 + a.m5 + a.m6 + a.m7 + a.m8 + a.m9 + a.m10),
         ROUND(a.m1 / a.n, 2),
         ROUND(a.m2 / a.n, 2),
         ROUND(a.m3 / a.n, 2),
         ROUND(a.m4 / a.n, 2),
         ROUND(a.m5 / a.n, 2),
         ROUND(a.m6 / a.n, 2),
         ROUND(a.m7 / a.n, 2),
         ROUND(a.m8 / a.n, 2),
         ROUND(a.m9 / a.n, 2),
         ROUND(a.m10 / a.n, 2),
         ROUND((a.m1 + a.m2 + a.m3 + a.m4 + a.m5 + a.m6 + a.m7 + a.m8 + a.m9 + a.m10) / (a.n * 10), 2),
         ROUND((a.m1 + a.m2 + a.m3 + a.m4 + a.m5 + a.m6 + a.m7 + a.m8 + a.m9 + a.m10) / a.n, 2)
  FROM agg a
  WHERE a.n > 0
  ON CONFLICT (instructor_id, term_id, subject_id) DO UPDATE SET
    total_responses = EXCLUDED.total_responses,
    m1_total = EXCLUDED.m1_total,
    m2_total = EXCLUDED.m2_total,
    m3_total = EXCLUDED.m3_total,
    m4_total = EXCLUDED.m4_total,
    m5_total = EXCLUDED.m5_total,
    m6_total = EXCLUDED.m6_total,
    m7_total = EXCLUDED.m7_total,
    m8_total = EXCLUDED.m8_total,
    m9_total = EXCLUDED.m9_total,
    m10_total = EXCLUDED.m10_total,
    grand_total = EXCLUDED.grand_total,
    m1_mean = EXCLUDED.m1_mean,
    m2_mean = EXCLUDED.m2_mean,
    m3_mean = EXCLUDED.m3_mean,
    m4_mean = EXCLUDED.m4_mean,
    m5_mean = EXCLUDED.m5_mean,
    m6_mean = EXCLUDED.m6_mean,
    m7_mean = EXCLUDED.m7_mean,
    m8_mean = EXCLUDED.m8_mean,
    m9_mean = EXCLUDED.m9_mean,
    m10_mean = EXCLUDED.m10_mean,
    overall_management_mean = EXCLUDED.overall_management_mean,
    average_per_response_management = EXCLUDED.average_per_response_management
  RETURNING total_responses
),
perf AS (
  INSERT INTO public.performance_results (
    instructor_id, term_id, subject_id, total_responses,
    p1_total, p2_total, p3_total, p4_total, p5_total, p6_total, p7_total, p8_total, p9_total, p10_total, grand_total,
    p1_mean, p2_mean, p3_mean, p4_mean, p5_mean, p6_mean, p7_mean, p8_mean, p9_mean, p10_mean,
    overall_performance_mean, average_per_response_performance
  )
  SELECT $1, $2, $3, a.n,
         a.p1, a.p2, a.p3, a.p4, a.p5, a.p6, a.p7, a.p8, a.p9, a.p10,
         (a.p1 + a.p2 + a.p3 + a.p4 + a.p5 + a.p6 + a.p7 + a.p8 + a.p9 + a.p10),
         ROUND(a.p1 / a.n, 2),
         ROUND(a.p2 / a.n, 2),
         ROUND(a.p3 / a.n, 2),
         ROUND(a.p4 / a.n, 2),
         ROUND(a.p5 / a.n, 2),
         ROUND(a.p6 / a.n, 2),
         ROUND(a.p7 / a.n, 2),
         ROUND(a.p8 / a.n, 2),
         ROUND(a.p9 / a.n, 2),
         ROUND(a.p10 / a.n, 2),
         ROUND((a.p1 + a.p2 + a.p3 + a.p4 + a.p5 + a.p6 + a.p7 + a.p8 + a.p9 + a.p10) / (a.n * 10), 2),
         ROUND((a.p1 + a.p2 + a.p3 + a.p4 + a.p5 + a.p6 + a.p7 + a.p8 + a.p9 + a.p10) / a.n, 2)
  FROM agg a
  WHERE a.n > 0
  ON CONFLICT (instructor_id, term_id, subject_id) DO UPDATE SET
    total_responses = EXCLUDED.total_responses,
    p1_total = EXCLUDED.p1_total,
    p2_total = EXCLUDED.p2_total,
    p3_total = EXCLUDED.p3_total,
    p4_total = EXCLUDED.p4_total,
    p5_total = EXCLUDED.p5_total,
    p6_total = EXCLUDED.p6_total,
    p7_total = EXCLUDED.p7_total,
    p8_total = EXCLUDED.p8_total,
    p9_total = EXCLUDED.p9_total,
    p10_total = EXCLUDED.p10_total,
    grand_total = EXCLUDED.grand_total,
    p1_mean = EXCLUDED.p1_mean,
    p2_mean = EXCLUDED.p2_mean,
    p3_mean = EXCLUDED.p3_mean,
    p4_mean = EXCLUDED.p4_mean,
    p5_mean = EXCLUDED.p5_mean,
    p6_mean = EXCLUDED.p6_mean,
    p7_mean = EXCLUDED.p7_mean,
    p8_mean = EXCLUDED.p8_mean,
    p9_mean = EXCLUDED.p9_mean,
    p10_mean = EXCLUDED.p10_mean,
    overall_performance_mean = EXCLUDED.overall_performance_mean,
    average_per_response_performance = EXCLUDED.average_per_response_performance
  RETURNING total_responses
)
SELECT
  (SELECT n FROM agg)              AS total_responses,
  (SELECT count(*) FROM mgmt)      AS management_rows,
  (SELECT count(*) FROM perf)      AS performance_rows,
  (SELECT count(*) FROM link)      AS instructor_subject_linked;
