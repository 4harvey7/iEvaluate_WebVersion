-- Rebuilds every SAST aggregate of the instructor + term from ALL raw rows, not just this run:
-- instructor_subjects links, management_results / performance_results per subject,
-- overall_total_survey and overall_question_means. Blank answers are left out of both the sum
-- and the count. Logic lives in public.recompute_sast (migrations/013_sast_null_aware_means.sql),
-- which takes the global recompute advisory lock (held until commit).
-- Parameters: instructor id, term id, subject id. The subject is not needed (every subject of
-- the pair is rebuilt); it stays in the parameter list so the node's interface is unchanged.
-- Returns one row: instructor_id, term_id, total_responses, management_mean, performance_mean,
-- combined_score_mean. A NULL term writes nothing.
SELECT * FROM public.recompute_sast($1::uuid, $2::uuid);
