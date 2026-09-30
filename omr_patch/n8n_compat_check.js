#!/usr/bin/env node
// n8n_compat_check.js - feed REAL Python OMR /process responses into the CURRENT n8n
// reconciler Code nodes and check that they parse them and flag only real problems.
//
//   1. python regression_harness.py ... --version NEW=patched --save RESULTS.json
//      (the harness keeps each full /process response, image dropped, OCR text masked)
//   2. node n8n_compat_check.js --results phone=PHONE.json --results long=LONG.json \
//          --results short=SHORT.json --labels GT.json --labels phone_labels.csv \
//          [--version NEW] [--compare ORIGINAL] [--workflow ../n8n/perfect-101-website.json] \
//          [--noise 0.02,0.05,0.10 --trials 200 --seed 1]
//
// A set named "phone*" goes through 'reconcile ocr and omr1' (phone branch), any other
// set through 'Website Reconcile OCR OMR' (website/scanner branch). The jsCode is taken
// from the workflow JSON and run with a small mock of n8n's $ / $input.
// Gemini is mocked with the GROUND-TRUTH letters (a perfect second reader) and synthetic
// instructor/subject text, so on a form the OMR read fully correctly every flag
// reason is spurious. Checked for every form:
//   * ratings: each comparisons[].python equals the OMR's rating_name (detected rows only)
//   * omr_debug: the weak-row reasons equal an independent count from omr_debug scores
//   * table_found / grid_source are passed through; python_raw_ratings === ratings
//   * text only from Gemini: with Gemini's text blank the instructor stays blank (no ocr_fields fallback)
//   * agreement: agreement_rows equals an independent count of rows where the OMR letter equals
//     the (mocked) Gemini letter; both blank = same, one blank = different
// With --noise, Gemini is ALSO mocked as ground truth plus random misreads (per row, at each rate:
// 70% a neighbouring column, 15% blank, 15% another column; a blank row becomes a random mark),
// --trials times per form, and the agreement flag rate is reported at AGREEMENT_MIN 0.80 / 0.90 /
// 0.96 (computed from agreement_rows, so one run covers every threshold).
// Exit code 1 when a check fails or a fully correct form gets a flag reason (except
// too_many_blanks on a form whose ground truth really has more than 10 blank rows).
// With --compare BASE, a flag BASE also raises on that same form is reported as
// pre-existing instead of failing (the reconciler thresholds, not the OMR change).
// Prints only file names, question keys and reason codes (no personal data).
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const args = { results: [], labels: [], version: 'NEW', compare: null, noise: '', trials: '200', seed: '1',
  workflow: path.join(__dirname, '..', 'n8n', 'perfect-101-website.json') };
for (let i = 2; i < process.argv.length; i++) {
  const k = process.argv[i].replace(/^--/, '');
  const v = process.argv[++i];
  if (k === 'results' || k === 'labels') args[k].push(v); else args[k] = v;
}
if (!args.results.length || !args.labels.length) {
  console.error('usage: node n8n_compat_check.js --results SET=FILE.json ... --labels FILE ... [--version NEW] [--compare ORIGINAL]');
  process.exit(2);
}

const wf = JSON.parse(fs.readFileSync(args.workflow, 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const codeOf = (name) => {
  const n = wf.nodes.find((x) => x.name === name);
  if (!n) throw new Error(`node not found in workflow: ${name}`);
  return n.parameters.jsCode;
};
const compiled = {};
async function runNode(name, input, nodes) {
  const $ = (ref) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, ref)) throw new Error(`Referenced node is unexecuted: ${ref}`);
    const v = nodes[ref];
    return { first: () => (v === undefined ? undefined : { json: v }), all: () => (v === undefined ? [] : [{ json: v }]), isExecuted: true };
  };
  const $input = { first: () => ({ json: input }), all: () => [{ json: input }], item: { json: input } };
  const quiet = { log() {}, warn() {}, error() {} };
  compiled[name] = compiled[name] || new AsyncFunction('$', '$input', 'Buffer', 'console', codeOf(name));
  return compiled[name]($, $input, Buffer, quiet);
}

// ---------------------------------------------------------------- labels
const Q = [...Array(10)].map((_, i) => `m${i + 1}`).concat([...Array(10)].map((_, i) => `p${i + 1}`));
const LETTERS = new Set(['O', 'VS', 'S', 'F', 'US']);
const normCell = (v) => {
  const s = v === null || v === undefined ? '' : String(v).trim().toUpperCase();
  if (['', '-', 'BLANK', 'NONE', 'NULL'].includes(s)) return null;
  if (s === '?' || s === 'X') return '?';
  if (LETTERS.has(s)) return s;
  throw new Error(`unknown label value ${v}`);
};
const truth = {};
for (const f of args.labels) {
  const text = fs.readFileSync(f, 'utf8').replace(/^﻿/, '');
  if (f.toLowerCase().endsWith('.json')) {
    const d = JSON.parse(text);
    const rows = Array.isArray(d) ? d : Object.entries(d).map(([k, v]) => ({ ...v, filename: k }));
    for (const r of rows) {
      if (Array.isArray(r.rows)) truth[r.filename.trim()] = [...r.rows].sort((a, b) => a.index - b.index).map((x) => normCell(x.answer));
      else truth[r.filename.trim()] = Q.map((q) => normCell(r[q]));
    }
  } else {
    const lines = text.split(/\r?\n/).filter((l) => l.trim());
    const head = lines[0].split(',').map((h) => h.trim().toLowerCase());
    for (const l of lines.slice(1)) {
      const c = l.split(',');
      const rec = Object.fromEntries(head.map((h, i) => [h, c[i]]));
      truth[rec.filename.trim()] = Q.map((q) => normCell(rec[q]));
    }
  }
}

// ---------------------------------------------------------------- mocks
const WEAK_TOP = 0.05, WEAK_MARGIN = 0.02;   // same as both reconcilers
const toScore = (v) => ({ O: 5, VS: 4, S: 3, F: 2, US: 1 }[String(v ?? '').trim().toUpperCase()] ?? null);
const ORDER = ['US', 'F', 'S', 'VS', 'O'];
function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let x = Math.imul(a ^ (a >>> 15), 1 | a);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}
// Ground truth with random Gemini misreads (see header). Returns the 20 letters (null = blank).
function noisy(t, rate, rnd) {
  return t.map((v) => {
    const cell = v === '?' ? null : v;
    if (rnd() >= rate) return cell;
    if (cell === null) return ORDER[Math.floor(rnd() * 5)];
    const k = ORDER.indexOf(cell);
    const r = rnd();
    if (r < 0.70) {
      const nb = [k - 1, k + 1].filter((j) => j >= 0 && j < 5);
      return ORDER[nb[Math.floor(rnd() * nb.length)]];
    }
    if (r < 0.85) return null;
    const others = ORDER.filter((x) => x !== cell);
    return others[Math.floor(rnd() * others.length)];
  });
}
function gemini(t, { blankText = false } = {}) {
  const ratings = {};
  Q.forEach((q, i) => { ratings[q] = t[i] && t[i] !== '?' ? t[i] : 'blank'; });
  const g = {
    instructor: blankText ? '' : 'Test Instructor', subject: blankText ? '' : 'TEST 101',
    ay_semester: '2026 1st Sem', remarks: '', date: '09/27/26', student_id: '0000',
    document_check: { is_sast_form: true, reason: 'SAST form' },
    scan_quality: { poor_quality: false, issues: [], reason: 'Good quality' },
    ratings,
  };
  return { candidates: [{ content: { parts: [{ text: JSON.stringify(g) }] } }] };
}
async function reconcile(isPhone, py, gem) {
  if (isPhone) {
    return (await runNode('reconcile ocr and omr1', {}, {
      'image to ocr and omr python': py, 'ai validation ocr and omr': gem,
      'SAST image from phone': { body: { task_id: 'compat', user_id: 'compat', term_id: null, image: 'data:image/jpeg;base64,AAAA' } },
    }))[0].json;
  }
  return (await runNode('Website Reconcile OCR OMR', gem, { 'Website Python OMR': py, 'Website AI Validation': gem }))[0].json;
}

// ---------------------------------------------------------------- run
let failures = 0;
const fail = (msg) => { failures++; console.log(`  FAIL ${msg}`); };

const NOISE = String(args.noise || '').split(',').map(Number).filter((x) => Number.isFinite(x) && x > 0);
const THRESHOLDS = [0.80, 0.90, 0.96];
const noiseAcc = {};
const pct = (a, b) => (b ? `${((100 * a) / b).toFixed(1)}%` : 'n/a');

(async () => {
  for (const spec of args.results) {
    const [set, file] = spec.split(/=(.*)/s);
    const res = JSON.parse(fs.readFileSync(file, 'utf8'));
    const isPhone = /^phone/i.test(set);
    const node = isPhone ? 'reconcile ocr and omr1' : 'Website Reconcile OCR OMR';
    noiseAcc[set] = {};
    for (const rate of NOISE) {
      const mk = () => ({ n: 0, agree: Object.fromEntries(THRESHOLDS.map((x) => [x, 0])), any: Object.fromEntries(THRESHOLDS.map((x) => [x, 0])) });
      noiseAcc[set][rate] = { correct: mk(), wrong: mk() };
    }
    // baseline first, so the main version can tell new flags from pre-existing ones
    const versions = (args.compare ? [args.compare] : []).concat([args.version]);
    const summary = {};
    const baseKinds = {};   // filename -> Set of reason kinds the baseline raised
    for (const ver of versions) {
      if (!res[ver]) { fail(`${set}: version ${ver} not in ${file}`); continue; }
      const s = summary[ver] = { forms: 0, correct: 0, correctFlagged: 0, wrongForms: 0, wrongCaught: 0,
        storedWrongRows: 0, reasons: {} };
      if (ver === args.version) console.log(`\n=== ${set} (${node}) - ${ver}`);
      for (const fn of Object.keys(res[ver]).sort()) {
        const r = res[ver][fn];
        const t = truth[fn];
        if (!t) continue;
        if (!r.response) { fail(`${set} ${fn}: no saved /process response (re-run the harness with --save)`); continue; }
        const py = r.response;
        s.forms++;
        const cells = [...py.ratings.management, ...py.ratings.performance];
        const pyLetters = cells.map((c) => (c.detected ? c.rating_name : null));
        const wrongOmr = Q.filter((q, i) => t[i] !== '?' && (pyLetters[i] ?? null) !== t[i]);
        const out = await reconcile(isPhone, py, gemini(t));

        // --- parse checks (only reported for the main version)
        const check = (cond, msg) => { if (!cond && ver === args.version) fail(`${set} ${fn}: ${msg}`); };
        check(out.comparisons.length === 20, 'comparisons length');
        out.comparisons.forEach((c, i) => check(c.python === toScore(pyLetters[i]), `ratings parse ${Q[i]}`));
        check(out.grid_source === (py.grid_source ?? null), 'grid_source pass-through');
        check(JSON.stringify(out.python_raw_ratings) === JSON.stringify(py.ratings), 'python_raw_ratings pass-through');
        if (!isPhone) check(out.table_found === py.table_found, 'table_found pass-through');
        const weak = new Set();
        for (const d of py.omr_debug) {
          const v = Object.values(d.scores).map(Number).sort((a, b) => b - a);
          if (v[0] < WEAK_TOP || v[0] - v[1] < WEAK_MARGIN) weak.add(d.row_index);
        }
        const weakDetected = Q.filter((q, i) => pyLetters[i] !== null && weak.has(i)).length;
        const lowConf = out.review_reasons.find((x) => x.startsWith('low_confidence_ocr_'));
        check(weakDetected >= 4 ? lowConf === `low_confidence_ocr_${weakDetected}_rows` : !lowConf, `omr_debug weak rows (${weakDetected})`);
        if (!isPhone) {
          const wb = Q.filter((q, i) => pyLetters[i] !== null && weak.has(i) && (t[i] === null)).length;
          const wbReason = out.review_reasons.find((x) => x.startsWith('python_weak_gemini_blank_'));
          check(wb ? wbReason === `python_weak_gemini_blank_${wb}_rows` : !wbReason, `omr_debug weak/gemini-blank rows (${wb})`);
        }
        const outBlank = await reconcile(isPhone, py, gemini(t, { blankText: true }));
        const instr = isPhone ? outBlank.instructor : outBlank.ocr_instructor;
        check(String(instr ?? '') === '', 'text only from Gemini (no ocr_fields fallback)');
        const agreeExpected = Q.filter((q, i) => toScore(pyLetters[i]) === toScore(t[i] === '?' ? null : t[i])).length;
        check(out.agreement_rows === agreeExpected, `agreement_rows ${out.agreement_rows} != ${agreeExpected}`);
        check(out.agreement_percent === Math.round(agreeExpected * 5), 'agreement_percent');

        // --- end-to-end: what n8n would store vs the truth
        const stored = Q.map((q) => out.ratings[q]);
        const storedWrong = Q.filter((q, i) => t[i] !== '?' && stored[i] !== toScore(t[i]));
        const reasons = out.review_reasons;
        const kind = (x) => x.replace(/_\d+(_of_20|_rows)?$/, '_N$1');
        for (const x of reasons) s.reasons[kind(x)] = (s.reasons[kind(x)] || 0) + 1;
        if (ver !== args.version) baseKinds[fn] = new Set(reasons.map(kind));
        // A form the raters really left mostly blank is SUPPOSED to be flagged too_many_blanks.
        const truthBlanks = t.filter((x) => x === null).length;
        const spurious = reasons.filter((x) => !(x.startsWith('too_many_blanks_') && truthBlanks > 10));
        if (!wrongOmr.length) {
          s.correct++;
          if (spurious.length) {
            s.correctFlagged++;
            // with --compare, only a flag the baseline does not also raise on this form is a failure
            const fresh = args.compare ? spurious.filter((x) => !(baseKinds[fn] && baseKinds[fn].has(kind(x)))) : spurious;
            if (ver === args.version && fresh.length) fail(`${set} ${fn}: fully correct OMR read but NEW flag(s): ${fresh.join(', ')}`);
            if (ver === args.version && fresh.length < spurious.length) s.preexisting = (s.preexisting || 0) + 1;
          }
        } else {
          s.wrongForms++;
          if (reasons.length) s.wrongCaught++;
          else s.storedWrongRows += storedWrong.length;
        }
        if (ver === args.version) {
          const tag = !wrongOmr.length ? 'correct' : `OMR wrong on ${wrongOmr.join(',')}`;
          const pre = args.compare && reasons.length && reasons.every((x) => baseKinds[fn] && baseKinds[fn].has(kind(x)))
            ? ` (same as ${args.compare})` : '';
          const fate = reasons.length ? `FLAGGED [${reasons.join(', ')}]${pre}`
            : (storedWrong.length ? `clean, would store ${storedWrong.length} wrong row(s): ${storedWrong.join(',')}` : 'clean');
          console.log(`  ${fn.padEnd(32)} ${tag.padEnd(28)} -> ${fate}`);
        }
        // --- noisy Gemini: agreement flag rate at several thresholds (main version only)
        if (ver === args.version && NOISE.length) {
          const other = reasons.filter((x) => !x.startsWith('omr_agreement_below_'));
          for (const rate of NOISE) {
            const acc = noiseAcc[set][rate];
            const rnd = mulberry32(Number(args.seed) * 1000003 + fn.length * 7919 + Math.round(rate * 1e4));
            for (let k = 0; k < Number(args.trials); k++) {
              const o = await reconcile(isPhone, py, gemini(noisy(t, rate, rnd)));
              const otherN = o.review_reasons.filter((x) => !x.startsWith('omr_agreement_below_'));
              const bucket = !wrongOmr.length ? acc.correct : acc.wrong;
              bucket.n++;
              for (const th of THRESHOLDS) {
                const minRows = Math.ceil(th * 20 - 1e-9);
                if (o.agreement_rows !== null && o.agreement_rows < minRows) bucket.agree[th]++;
                if (otherN.length || (o.agreement_rows !== null && o.agreement_rows < minRows)) bucket.any[th]++;
              }
            }
            void other;
          }
        }
      }
    }
    for (const [ver, s] of Object.entries(summary)) {
      console.log(`  [${set} ${ver}] forms ${s.forms}: fully correct ${s.correct} (spuriously flagged ${s.correctFlagged}${s.preexisting ? `, ${s.preexisting} of them also by ${args.compare}` : ''}); `
        + `with OMR errors ${s.wrongForms} (flagged ${s.wrongCaught}); wrong rows stored unflagged ${s.storedWrongRows}; `
        + `reasons ${JSON.stringify(s.reasons)}`);
    }
  }
  if (NOISE.length) {
    console.log(`\n=== noisy Gemini (${args.trials} trials per form; flag rate = share of form-trials flagged)`);
    for (const [set, byRate] of Object.entries(noiseAcc)) {
      for (const [rate, acc] of Object.entries(byRate)) {
        const line = (b, label) => `${label} ` + THRESHOLDS.map((th) =>
          `@${Math.round(th * 100)}%: agreement ${pct(b.agree[th], b.n)} / any reason ${pct(b.any[th], b.n)}`).join('; ');
        console.log(`  [${set} noise ${(Number(rate) * 100).toFixed(0)}%/row] ${line(acc.correct, `fully correct forms (${acc.correct.n / Number(args.trials)}):`)}`);
        if (acc.wrong.n) console.log(`  [${set} noise ${(Number(rate) * 100).toFixed(0)}%/row] ${line(acc.wrong, `forms with OMR errors (${acc.wrong.n / Number(args.trials)}), caught:`)}`);
      }
    }
  }
  console.log(failures ? `\n${failures} check(s) FAILED` : '\nall n8n compatibility checks passed');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
