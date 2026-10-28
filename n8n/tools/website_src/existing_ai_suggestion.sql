-- Get Existing AI Suggestion1 (single-scan remark AI path): ALWAYS exactly one row, so Merge2
-- (3 inputs, combine by position) stays aligned even before the first analysis exists.
-- previous_remarks: the last 100 stored remarks of this instructor and term, newest first,
-- remark text and tone only (no student id, no names). Same shape as previous_remarks in
-- Get Previous Instructor Analysis1. The new remark is stored after the AI call, so it is not in here.
-- Parameters: instructor id, term id.
SELECT
    p.instructor_id,
    p.term_id,
    ai.ai_suggestion,
    ai.student_interpretation,
    ai.positive_themes,
    ai.improvement_insights,
    ai.overall_tone,
    COALESCE(
        (
            SELECT jsonb_agg(
                jsonb_build_object('remark', x.remark, 'tone', x.tone)
                ORDER BY x.created_at DESC
            )
            FROM (
                SELECT sr.remark, sr.tone, sr.created_at
                FROM public.student_remarks sr
                WHERE sr.instructor_id = p.instructor_id
                  AND sr.term_id = p.term_id
                ORDER BY sr.created_at DESC
                LIMIT 100
            ) x
        ),
        '[]'::jsonb
    ) AS previous_remarks
FROM (SELECT $1::uuid AS instructor_id, $2::uuid AS term_id) p
LEFT JOIN LATERAL (
    SELECT s.ai_suggestion, s.student_interpretation, s.positive_themes, s.improvement_insights, s.overall_tone
    FROM public.instructor_ai_suggestions s
    WHERE s.instructor_id = p.instructor_id
      AND s.term_id = p.term_id
    LIMIT 1
) ai ON true;
