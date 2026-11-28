// The Upload page (remitwatch/static/upload.html).
//
// What it does, in order, for each file dropped:
//  1. Looks at the file's row of column names, here in the browser, before anything is sent: is it one of the five EMA
//     exports (the A/R report, the Production report, Encounter & Charge Lines, ERA & Payment Line Adjustments, Claim &
//     Claim Status), is it the one the row it was dropped on asks for, and does it have every column RemitWatch needs. The
//     rules are the server's own (remitwatch.ema_reports.recognize and remitwatch.ema_exports.detect_family), served by
//     GET /api/ema/exports. An .xlsx file is read from its zip directory and the first rows of its first sheet only.
//  2. Works out the SHA-256 of the file as it is (an inline SHA-256 that reads the file in 4 MB slices: no library from
//     elsewhere, and never the whole file in memory). RemitWatch knows a file by it: a file it has already is not sent.
//  3. Sends a report, or any file over 20 MB, in pieces of exactly 4 MB (the last one shorter) to /api/uploads/chunk,
//     each with its own SHA-256; a text report is compressed on the way with CompressionStream('gzip') where the browser
//     has it. A piece whose answer is lost is sent again (the server takes it once). Pause and Resume stop and restart
//     between pieces; an upload cut off (a closed tab, a lost connection) carries on from the first piece the server lacks
//     when the same file is chosen again. Then /api/uploads/{id}/finish has the whole file checked on the server.
//  4. Shows a plain result per file: what it was recognised as, its dates, rows, what it replaced, holds, or "Already
//     have this; nothing changed", updated once the cycle has read it.
// On the practice's server, visit notes and small files still go in one piece to /api/uploads (as before), with the
// panels for notes and for files waiting to be read. The pure parts (SHA-256, header reading, recognition, cutting into
// pieces) load in Node too, for tests/test_upload_chunks.py.
(function (root) {
  'use strict';
  const CHUNK = 4 * 1024 * 1024;                  // uploads.CHUNK_BYTES
  const CHUNK_FROM = 20 * 1024 * 1024;            // uploads.CHUNK_FROM_BYTES
  const PEEK = 65536;
  const TEXT_EXT = /\.(csv|tsv|txt)$/i;
  const NOTE_EXT = /\.(pdf|docx|rtf|html?)$/i;
  const KEY_OF = {ar_report: 'ar', production: 'production', charge_lines: 'charge_lines', era_lines: 'era_lines', claim_status: 'claim_status'};

  // ------------------------------------------------------------------------------------------------------- SHA-256
  const K = new Int32Array([0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb,
    0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f,
    0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);

  // An incremental SHA-256: update() with as many pieces as the file comes in, then hex().
  class Sha256 {
    constructor() {
      this.h = new Int32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
      this.w = new Int32Array(64);
      this.buf = new Uint8Array(64);
      this.fill = 0;
      this.length = 0;
    }
    update(data) {
      const n = data.length;
      let i = 0;
      this.length += n;
      if (this.fill) {
        const take = Math.min(64 - this.fill, n);
        this.buf.set(data.subarray(0, take), this.fill);
        this.fill += take;
        i = take;
        if (this.fill < 64) return this;
        this.block(this.buf, 0);
        this.fill = 0;
      }
      for (; i + 64 <= n; i += 64) this.block(data, i);
      if (i < n) { this.buf.set(data.subarray(i), 0); this.fill = n - i; }
      return this;
    }
    block(p, o) {
      const w = this.w, h = this.h;
      for (let t = 0; t < 16; t++) {
        const j = o + 4 * t;
        w[t] = (p[j] << 24) | (p[j + 1] << 16) | (p[j + 2] << 8) | p[j + 3];
      }
      for (let t = 16; t < 64; t++) {
        const x = w[t - 15], y = w[t - 2];
        const s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
        const s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
        w[t] = (w[t - 16] + s0 + w[t - 7] + s1) | 0;
      }
      let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], k = h[7];
      for (let t = 0; t < 64; t++) {
        const t1 = (k + (((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7))) + ((e & f) ^ (~e & g)) + K[t] + w[t]) | 0;
        const t2 = ((((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10))) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        k = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h[0] = (h[0] + a) | 0; h[1] = (h[1] + b) | 0; h[2] = (h[2] + c) | 0; h[3] = (h[3] + d) | 0;
      h[4] = (h[4] + e) | 0; h[5] = (h[5] + f) | 0; h[6] = (h[6] + g) | 0; h[7] = (h[7] + k) | 0;
    }
    hex() {
      const bits = this.length * 8;
      const pad = new Uint8Array((this.fill < 56 ? 64 : 128) - this.fill);
      pad[0] = 0x80;
      const hi = Math.floor(bits / 4294967296), lo = bits >>> 0, L = pad.length;
      pad[L - 8] = hi >>> 24; pad[L - 7] = (hi >>> 16) & 255; pad[L - 6] = (hi >>> 8) & 255; pad[L - 5] = hi & 255;
      pad[L - 4] = lo >>> 24; pad[L - 3] = (lo >>> 16) & 255; pad[L - 2] = (lo >>> 8) & 255; pad[L - 1] = lo & 255;
      const length = this.length;
      this.update(pad);
      this.length = length;
      let out = '';
      for (let i = 0; i < 8; i++) out += (this.h[i] >>> 0).toString(16).padStart(8, '0');
      return out;
    }
  }
  const sha256Hex = bytes => new Sha256().update(bytes).hex();

  // ----------------------------------------------------------------------------------- the row of column names
  // ema_reports.norm: stripped, lower-cased, spaces collapsed, no spaces around "-" and "/"
  const norm = h => String(h == null ? '' : h).replace(/﻿/g, '').split(/\s+/).filter(Boolean).join(' ').toLowerCase()
    .replace(/\s*([-/])\s*/g, '$1');

  // ema_reports.detect_report
  function detectReport(R, cells) {
    const names = new Set(cells.filter(c => c != null && c !== '').map(norm));
    const prodOk = !names.has('activity type');
    const all = list => list.every(n => names.has(n));
    const near = r => {
      const lay = R.reports[r];
      return lay.core.filter(n => !names.has(n)).length <= 2 && lay.required.filter(n => names.has(n)).length >= 0.75 * lay.required.length;
    };
    if (prodOk && all(R.reports.production.core)) return 'production';
    if (all(R.reports.ar_report.core)) return 'ar_report';
    if (prodOk && near('production')) return 'production';
    if (near('ar_report')) return 'ar_report';
    return null;
  }

  // ema_reports._find_header: the first of the first HEADER_SCAN rows holding a report's core names, else a near match
  function findReportHeader(R, rows) {
    let near = null;
    for (let i = 0; i < Math.min(rows.length, R.header_scan); i++) {
      const cells = rows[i].filter(c => c != null && c !== '');
      const names = new Set(cells.map(norm));
      const full = (!names.has('activity type') && R.reports.production.core.every(n => names.has(n))) || R.reports.ar_report.core.every(n => names.has(n));
      if (full) return {report: detectReport(R, cells), index: i, row: rows[i]};
      if (!near) { const rep = detectReport(R, cells); if (rep) near = {report: rep, index: i, row: rows[i]}; }
    }
    return near;
  }

  // what the report's reader would take from this row (ema_reports._build): a name it reads written twice (the file is
  // refused), and the required names it lacks. The A/R reader also reads any other aging bucket or a Total A/R column.
  function reportColumns(R, report, row) {
    const lay = R.reports[report];
    const read = new Set(lay.required.concat(lay.optional || []));
    const bucket = n => report === 'ar_report' && ((n.endsWith('a/r ($) (aging)') && n !== 'current a/r ($) (aging)' && !n.startsWith('total'))
      || n.startsWith('total a/r'));
    const seen = new Set();
    let twice = null;
    row.forEach(c => {
      const n = norm(c);
      if (!read.has(n) && !bucket(n)) return;
      if (seen.has(n) && twice == null) twice = String(c == null ? '' : c).trim() || n;
      seen.add(n);
    });
    return {missing: lay.required.filter(n => !seen.has(n)), twice};
  }

  // ema_exports.header_row, then ema_exports.detect_family
  function headerRowIndex(R, rows) {
    let best = 0, bestN = 0;
    for (let i = 0; i < Math.min(R.header_scan, rows.length); i++) {
      const n = rows[i].filter(v => typeof v === 'string' && R.canon[v.toLowerCase().trim()] !== undefined).length;
      if (n > bestN) { best = i; bestN = n; }
    }
    if (bestN >= 3) return best;
    for (let i = 0; i < Math.min(10, rows.length); i++) if (rows[i].filter(v => typeof v === 'string' && v.trim() !== '').length >= 5) return i;
    return 0;
  }
  function detectFamily(R, columns) {
    const canon = new Set(columns.filter(c => c != null).map(c => R.canon[String(c).toLowerCase().trim()]).filter(Boolean));
    if (!R.key_columns.some(k => canon.has(k))) return null;
    if (canon.has('adjustment_status') || (canon.has('carc') && canon.has('rarc') && !canon.has('activity_type'))) return 'era_lines';
    if (canon.has('activity_type')) return 'charge_lines';
    if (canon.has('denial_amount') || canon.has('claim_status')) return 'claim_status';
    return null;
  }

  // Which of the five exports these first rows are, and what they lack. Reports are looked for first (as the server does).
  function lookAtRows(R, rows) {
    const hit = findReportHeader(R, rows);
    if (hit && hit.report) {
      const cols = reportColumns(R, hit.report, hit.row);
      return {hint: hit.report, key: KEY_OF[hit.report], kind: 'report', header: hit.index, missing: cols.missing, twice: cols.twice};
    }
    if (!rows.length) return {hint: null, key: null, kind: 'unknown', missing: []};
    const at = headerRowIndex(R, rows);
    const columns = rows[at] || [];
    const family = detectFamily(R, columns);
    if (!family) return {hint: null, key: null, kind: 'unknown', missing: [], columns: columns.filter(c => c).slice(0, 12)};
    const have = new Set(columns.filter(c => c != null).map(c => R.canon[String(c).toLowerCase().trim()]).filter(Boolean));
    return {hint: family, key: KEY_OF[family], kind: 'export', header: at, missing: (R.want[family] || []).filter(k => !have.has(k))};
  }

  // ------------------------------------------------------------------------------------- reading the first rows
  function decodeHead(bytes, truncated) {
    let text;
    try { text = new TextDecoder('utf-8', {fatal: true}).decode(bytes); }
    catch (e) {
      try { text = new TextDecoder('utf-8', {fatal: true}).decode(bytes.subarray(0, Math.max(0, bytes.length - 3))); }
      catch (e2) { text = new TextDecoder('windows-1252').decode(bytes); }
    }
    text = text.replace(/^﻿/, '');
    if (truncated) { const cut = Math.max(text.lastIndexOf('\n'), text.lastIndexOf('\r')); text = cut >= 0 ? text.slice(0, cut + 1) : ''; }
    return text;
  }

  // delimited text as records (quotes, doubled quotes, line breaks inside quotes), the first `limit` of them
  function csvRecords(text, delim, limit) {
    const out = [];
    let row = [], cell = '', q = false, i = 0;
    const n = text.length;
    while (i < n && out.length < limit) {
      const ch = text[i];
      if (q) {
        if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i += 2; continue; } q = false; i++; continue; }
        cell += ch; i++; continue;
      }
      if (ch === '"' && cell === '') { q = true; i++; continue; }
      if (ch === delim) { row.push(cell); cell = ''; i++; continue; }
      if (ch === '\r' || ch === '\n') {
        row.push(cell); out.push(row); row = []; cell = '';
        i += (ch === '\r' && text[i + 1] === '\n') ? 2 : 1;
        continue;
      }
      cell += ch; i++;
    }
    if (out.length < limit && (cell !== '' || row.length)) { row.push(cell); out.push(row); }
    return out;
  }

  // ema_reports.recognize for delimited text: the delimiter whose header row holds the most names a reader knows
  function textRows(R, text) {
    const known = new Set(R.report_names);
    const canon = R.canon;
    let best = null;
    for (const d of [',', '\t', '|', ';']) {
      const rows = csvRecords(text, d, R.header_scan);
      const hit = findReportHeader(R, rows);
      const score = hit ? hit.row.filter(c => known.has(norm(c))).length : 0;
      const fam = rows.length ? rows[headerRowIndex(R, rows)].filter(c => canon[String(c).toLowerCase().trim()] !== undefined).length : 0;
      const s = score * 1000 + fam;
      if (!best || s > best.s) best = {s, rows, d};
    }
    return best ? best.rows : [];
  }

  const ENT = {lt: '<', gt: '>', amp: '&', quot: '"', apos: "'"};
  const unxml = s => s.replace(/&(#x[0-9a-f]+|#\d+|lt|gt|amp|quot|apos);/gi, (m, e) => e[0] === '#'
    ? String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10)) : ENT[e.toLowerCase()]);
  const u16 = (b, o) => b[o] | (b[o + 1] << 8);
  const u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;

  // the entries of a zip, from its central directory (read from the end of the file; nothing else is read)
  async function zipEntries(file) {
    const tailLen = Math.min(file.size, 65557);
    const tail = new Uint8Array(await file.slice(file.size - tailLen).arrayBuffer());
    let at = -1;
    for (let i = tail.length - 22; i >= 0; i--) if (u32(tail, i) === 0x06054b50) { at = i; break; }
    if (at < 0) return null;
    const count = u16(tail, at + 10), cdSize = u32(tail, at + 12), cdOffset = u32(tail, at + 16);
    if (cdOffset === 0xffffffff || cdSize === 0xffffffff || count > 5000 || cdOffset + cdSize > file.size) return null;
    const cd = new Uint8Array(await file.slice(cdOffset, cdOffset + cdSize).arrayBuffer());
    const out = {};
    let p = 0;
    for (let k = 0; k < count && p + 46 <= cd.length; k++) {
      if (u32(cd, p) !== 0x02014b50) break;
      const nameLen = u16(cd, p + 28), extra = u16(cd, p + 30), comment = u16(cd, p + 32);
      const name = new TextDecoder().decode(cd.subarray(p + 46, p + 46 + nameLen));
      out[name] = {method: u16(cd, p + 10), csize: u32(cd, p + 20), size: u32(cd, p + 24), offset: u32(cd, p + 42)};
      p += 46 + nameLen + extra + comment;
    }
    return out;
  }

  // one zip entry as text, streamed; `enough(text)` says when to stop reading
  async function zipText(file, e, enough) {
    const head = new Uint8Array(await file.slice(e.offset, e.offset + 30).arrayBuffer());
    if (u32(head, 0) !== 0x04034b50) return null;
    const start = e.offset + 30 + u16(head, 26) + u16(head, 28);
    let stream = file.slice(start, start + e.csize).stream();
    if (e.method === 8) {
      if (typeof DecompressionStream === 'undefined') return null;
      stream = stream.pipeThrough(new DecompressionStream('deflate-raw'));
    } else if (e.method !== 0) return null;
    const reader = stream.pipeThrough(new TextDecoderStream()).getReader();
    let text = '';
    try {
      for (;;) {
        const {value, done} = await reader.read();
        if (done) break;
        text += value;
        if (enough(text) || text.length > 64 << 20) break;
      }
    } finally { reader.cancel().catch(() => {}); }
    return text;
  }

  const colIndex = ref => { let n = 0; for (const ch of (ref.match(/^[A-Z]+/i) || [''])[0].toUpperCase()) n = n * 26 + ch.charCodeAt(0) - 64; return n - 1; };
  const ROW_RE = /<(?:\w+:)?row\b[^>]*?(?:\/>|>([\s\S]*?)<\/(?:\w+:)?row>)/g;
  const CELL_RE = /<(?:\w+:)?c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:\w+:)?c>)/g;
  const tTexts = s => { let out = ''; s.replace(/<(?:\w+:)?rPh\b[\s\S]*?<\/(?:\w+:)?rPh>/g, '').replace(/<(?:\w+:)?t\b[^>]*>([\s\S]*?)<\/(?:\w+:)?t>/g, (m, t) => { out += unxml(t); return m; }); return out; };

  // The first `limit` rows of an .xlsx file's first sheet, cells as text (shared strings looked up), or null when it is
  // not a workbook this page can read (the server then decides). Only the zip directory, the start of the sheet and the
  // start of the shared strings are read.
  async function readXlsxRows(file, limit) {
    const entries = await zipEntries(file);
    if (!entries || !entries['xl/workbook.xml']) return null;
    let sheet = null;
    try {
      const wb = await zipText(file, entries['xl/workbook.xml'], () => false);
      const rels = entries['xl/_rels/workbook.xml.rels'] ? await zipText(file, entries['xl/_rels/workbook.xml.rels'], () => false) : '';
      const first = (wb.match(/<(?:\w+:)?sheet\b[^>]*?\br:id="([^"]+)"/) || [])[1];
      const rel = first && (rels.match(new RegExp('<Relationship\\b[^>]*\\bId="' + first + '"[^>]*\\bTarget="([^"]+)"')) ||
        rels.match(new RegExp('<Relationship\\b[^>]*\\bTarget="([^"]+)"[^>]*\\bId="' + first + '"')) || [])[1];
      if (rel) sheet = rel.startsWith('/') ? rel.slice(1) : 'xl/' + rel.replace(/^\.\//, '');
    } catch (e) { sheet = null; }
    if (!sheet || !entries[sheet]) sheet = Object.keys(entries).filter(n => /^xl\/worksheets\/[^/]+\.xml$/.test(n)).sort()[0];
    if (!sheet) return null;
    const xml = await zipText(file, entries[sheet], t => { ROW_RE.lastIndex = 0; let n = 0; while (ROW_RE.exec(t) && ++n < limit); return n >= limit; });
    if (xml == null) return null;
    const rows = [];
    const need = new Set();
    ROW_RE.lastIndex = 0;
    let m;
    while ((m = ROW_RE.exec(xml)) && rows.length < limit) {
      const row = [];
      let pos = 0;
      CELL_RE.lastIndex = 0;
      let c;
      while ((c = CELL_RE.exec(m[1] || ''))) {
        const attrs = c[1], inner = c[2] || '';
        const r = (attrs.match(/\br="([A-Z]+)\d*"/i) || [])[1];
        const at = r ? colIndex(r) : pos;
        pos = at + 1;
        const t = (attrs.match(/\bt="(\w+)"/) || [])[1] || 'n';
        const v = (inner.match(/<(?:\w+:)?v\b[^>]*>([\s\S]*?)<\/(?:\w+:)?v>/) || [])[1];
        let val = '';
        if (t === 's' && v != null) { val = {shared: parseInt(v, 10)}; need.add(val.shared); }
        else if (t === 'inlineStr') val = tTexts(inner);
        else if (v != null) val = unxml(v);
        while (row.length < at) row.push('');
        row[at] = val;
      }
      rows.push(row);
    }
    if (need.size && entries['xl/sharedStrings.xml']) {
      const max = Math.max(...need);
      const SI = /<(?:\w+:)?si\b[^>]*>([\s\S]*?)<\/(?:\w+:)?si>|<(?:\w+:)?si\b[^>]*\/>/g;
      const sst = await zipText(file, entries['xl/sharedStrings.xml'], t => { SI.lastIndex = 0; let n = 0; while (SI.exec(t)) if (++n > max) return true; return false; });
      const strings = [];
      SI.lastIndex = 0;
      let s;
      while ((s = SI.exec(sst || '')) && strings.length <= max) strings.push(s[1] ? tTexts(s[1]) : '');
      rows.forEach(row => row.forEach((v, i) => { if (v && typeof v === 'object') row[i] = strings[v.shared] != null ? strings[v.shared] : ''; }));
    } else rows.forEach(row => row.forEach((v, i) => { if (v && typeof v === 'object') row[i] = ''; }));
    return rows;
  }

  // What a file is, from its first bytes and its row of column names:
  // {kind: 'report' | 'export' | 'x12' | 'note' | 'zip' | 'old_excel' | 'unknown', key, hint, missing, text, ...}
  async function lookAtFile(R, file) {
    const head = new Uint8Array(await file.slice(0, PEEK + 1).arrayBuffer());
    const truncated = head.length > PEEK;
    const bytes = head.subarray(0, PEEK);
    if (bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46) return {kind: 'note'};
    if (bytes[0] === 0xd0 && bytes[1] === 0xcf && bytes[2] === 0x11 && bytes[3] === 0xe0) return {kind: 'old_excel'};
    if (bytes[0] === 0x50 && bytes[1] === 0x4b) {
      const entries = await zipEntries(file).catch(() => null);
      if (entries && entries['xl/workbook.xml']) {
        const rows = await readXlsxRows(file, R.header_scan).catch(() => null);
        return rows ? Object.assign(lookAtRows(R, rows), {fmt: 'xlsx', text: false}) : {kind: 'unknown', fmt: 'xlsx', unread: true};
      }
      if (entries && entries['word/document.xml']) return {kind: 'note'};
      return {kind: 'zip'};
    }
    const text = decodeHead(bytes, truncated);
    if (/^[\s﻿]*ISA/.test(text)) return {kind: 'x12'};
    if (bytes.includes(0)) return {kind: 'unknown'};
    const look = lookAtRows(R, textRows(R, text));
    if (look.kind === 'unknown' && NOTE_EXT.test(file.name)) return {kind: 'note'};
    if (look.kind === 'unknown' && /\.txt$/i.test(file.name)) return Object.assign(look, {kind: 'note_or_text', text: true});
    return Object.assign(look, {fmt: 'csv', text: true});
  }

  // ------------------------------------------------------------------------------------------- cutting into pieces
  // Pieces of exactly CHUNK bytes from a stream, the last one shorter, each marked whether it is the last.
  async function* cutPieces(stream) {
    const reader = stream.getReader();
    let buf = new Uint8Array(CHUNK), fill = 0;
    try {
      for (;;) {
        const {value, done} = await reader.read();
        if (done) break;
        const v = value instanceof Uint8Array ? value : new Uint8Array(value);
        let off = 0;
        while (off < v.length) {
          if (fill === CHUNK) { yield {data: buf, last: false}; buf = new Uint8Array(CHUNK); fill = 0; }
          const take = Math.min(CHUNK - fill, v.length - off);
          buf.set(v.subarray(off, off + take), fill);
          fill += take;
          off += take;
        }
      }
      if (fill > 0) yield {data: fill === CHUNK ? buf : buf.subarray(0, fill), last: true};
    } finally {
      reader.cancel().catch(() => {});
    }
  }

  // The bytes to send: the file from `from`, compressed on the way when gz. onRead(n) counts the file's own bytes read.
  function sourceStream(file, gz, from, onRead) {
    const counted = file.slice(from).stream().pipeThrough(new TransformStream({transform(chunk, ctl) { onRead(chunk.byteLength); ctl.enqueue(chunk); }}));
    return gz ? counted.pipeThrough(new CompressionStream('gzip')) : counted;
  }

  async function sha256File(file, onProgress, gate) {
    const h = new Sha256();
    for (let off = 0; off < file.size; off += CHUNK) {
      if (gate) await gate();
      h.update(new Uint8Array(await file.slice(off, Math.min(file.size, off + CHUNK)).arrayBuffer()));
      if (onProgress) onProgress(Math.min(file.size, off + CHUNK));
    }
    return h.hex();
  }

  // What became of a file the cycle (or run) has looked at, from the server's 'applied': a report's catalog status, the
  // hand-off's result word, or 'read'. Only a file RemitWatch read is green; one kept and not read yet is amber; one it
  // did not read (refused, too large, none of its files, failed, discarded) is red. A word this page does not know is
  // amber, never green.
  const OUTCOME = {current: 'applied', partly_replaced: 'applied', replaced: 'applied', superseded: 'applied', applied: 'applied', read: 'applied',
    already: 'already', held: 'held', waiting: 'unread', reread: 'unread',
    not_read: 'notread', too_large: 'notread', failed: 'notread', refused: 'notread', discarded: 'notread'};
  const outcomeChip = applied => OUTCOME[applied] || 'unread';
  // the chip of an upload as the server describes it (a finish's answer, a status, a row of the recent list)
  const uploadChip = u => u.state === 'finished' ? (u.applied ? outcomeChip(u.applied) : 'waiting') : u.state;

  const api = {Sha256, sha256Hex, norm, detectReport, findReportHeader, reportColumns, headerRowIndex, detectFamily, lookAtRows,
    decodeHead, csvRecords, textRows, zipEntries, readXlsxRows, lookAtFile, cutPieces, sourceStream, sha256File, CHUNK, CHUNK_FROM,
    OUTCOME, outcomeChip, uploadChip};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RemitUpload = api;
  if (typeof document === 'undefined' || !document.getElementById('exports')) return;

  // =================================================================================================== the page
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const money = c => '$' + ((c || 0) / 100).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const day = d => d ? new Date(String(d).slice(0, 10) + 'T12:00:00').toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'}) : '';
  const when = d => d ? new Date(d).toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : '';
  const mb = n => n >= 1024 * 1024 ? (n / 1048576).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  let info = {show_phi: false};
  let EX = null;                // GET /api/ema/exports
  const XHR = {'X-Requested-With': 'remitwatch'};

  async function apiCall(path, opts) {
    opts = opts || {};
    if (opts.method && opts.method !== 'GET') opts.headers = Object.assign({}, XHR, {'Content-Type': 'application/json'}, opts.headers || {});
    const r = await fetch(path, Object.assign({cache: 'no-store', credentials: 'same-origin'}, opts));
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok) { const e = new Error((j && j.detail) || r.statusText); e.status = r.status; e.body = j; throw e; }
    return j;
  }

  // one file at a time, its bytes as the body: a large export shows how far it has got. The file's name (often the
  // patient's, for a note) always goes in a header, never in the address: an access log keeps addresses. (The server
  // reads the header since this page has sent it; the page and the server ship together.)
  function send(file, findingId) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest();
      const q = new URLSearchParams(); if (findingId) q.set('finding_id', findingId);
      x.open('POST', '/api/uploads' + (q.toString() ? '?' + q.toString() : ''));
      x.setRequestHeader('X-File-Name', encodeURIComponent(file.name));
      x.setRequestHeader('X-Requested-With', 'remitwatch');
      x.setRequestHeader('Content-Type', 'application/octet-stream');
      x.upload.onprogress = e => { if (e.lengthComputable && send.onprogress) send.onprogress(e.loaded / e.total); };
      x.onload = () => { let j = null; try { j = JSON.parse(x.responseText); } catch (e) {}
        if (x.status >= 200 && x.status < 300) resolve(j); else reject(new Error((j && j.detail) || ('the server answered ' + x.status))); };
      x.onerror = () => reject(new Error('the connection failed'));
      x.send(file);
    });
  }

  const CHIP = {queued: ['to be read', 'warn'], ready: ['to be read', 'warn'], done: ['read', 'ok'], already: ['already in RemitWatch', ''], rejected: ['not read', 'no'], failed: ['failed', 'no'],
    matched: ['note matched', 'ok'], choose: ['pick its visit', 'warn'], unmatched: ['note not matched yet', 'warn'], zip: ['zip', ''], removed: ['taken back', ''],
    checking: ['checking', ''], hashing: ['checking', ''], sending: ['sending', ''], paused: ['paused', 'warn'], verifying: ['checking on the server', ''],
    waiting: ['to be read', 'warn'], applied: ['read', 'ok'], held: ['held', 'warn'], refused: ['not taken', 'no'], wrong: ['not this export', 'no'],
    unread: ['not read yet', 'warn'], notread: ['not read', 'no'], stuck: ['upload not read', 'no'],
    receiving: ['unfinished', 'warn'], finished: ['received', 'ok'], stopped: ['not sent', 'no'], overdue: ['overdue', 'no'], fresh: ['up to date', 'ok']};
  const chip = s => { const c = CHIP[s] || [s, '']; return `<span class="chip ${c[1]}">${esc(c[0])}</span>`; };
  const linesHtml = ls => (ls || []).length ? `<ul>${ls.map(l => `<li>${esc(l)}</li>`).join('')}</ul>` : '';

  // ----------------------------------------------------------------------------------- the checklist of exports
  // a row's chip and the colour of its state line, from the server's flag (older servers: from overdue and waiting)
  const rowFlag = e => e.flag || (e.waiting ? 'waiting' : e.overdue ? 'overdue' : 'fresh');
  const FLAG_TONE = {fresh: 'ok', waiting: 'warn', held: 'warn', unread: 'warn', overdue: 'no', stuck: 'no'};
  const FLAG_CHIP = {fresh: 'fresh', waiting: 'waiting', held: 'held', unread: 'unread', overdue: 'overdue', stuck: 'stuck'};
  const arrivedHtml = e => `<div>${esc(e.last_line)}</div><div class="status-line ${FLAG_TONE[rowFlag(e)] || ''}">${esc(e.state_line)}</div>`
    + (e.host_line ? `<div class="small warn" data-host>${esc(e.host_line)}</div>` : '');

  let leadKey = null;
  function exportRow(e) {
    const cols = e.columns.map(c => `<li><b>${esc(c.column)}</b>${c.why ? ': ' + esc(c.why) : ''}</li>`).join('');
    const h = e.howto || {};
    const who = h.at ? `Saved ${esc(when(h.at))}${h.name ? ' by ' + esc(h.name) : ''}${h.by ? ' (signed in as ' + esc(h.by) + ')' : ''}.` : '';
    return `<div class="export" data-key="${esc(e.key)}">
      <div>
        <div class="top"><h3>${esc(e.title)}</h3>${chip(FLAG_CHIP[rowFlag(e)] || 'fresh')}</div>
        <div class="big mt-1">${esc(e.purpose)}</div>
        <dl><dt>Last arrived</dt><dd data-arrived>${arrivedHtml(e)}</dd></dl>
        <details class="exp-more"><summary class="small">What to pull: filters, columns and format</summary>
        <dl>
          <dt>Filters</dt><dd><ul>${e.filters.map(f => `<li>${esc(f)}</li>`).join('')}</ul></dd>
          <dt>Columns</dt><dd>${esc(e.columns_note || '')} <details><summary class="small">The ${e.columns.length} columns it must have (checked when you drop it)</summary><ul class="cols">${cols}</ul></details></dd>
          <dt>Format</dt><dd>${esc(e.format)}</dd>
          <dt>How often</dt><dd>${esc(e.cadence)}</dd>
        </dl></details>
      </div>
      <div>
        <div class="howto">
          <div class="top"><b class="small">How we pull this in our EMA</b><button class="small" data-edit>Edit</button></div>
          ${h.text ? `<div class="text">${esc(h.text)}</div>` : `<div class="ask">${esc(e.ask || '')}</div>`}
          <div class="small muted">${who}${h.versions ? ` <a href="#" data-hist>${h.versions} earlier version${h.versions === 1 ? '' : 's'}</a>` : ''}</div>
          <div class="hist" hidden></div>
        </div>
        <div class="row mt-3"><span class="attach"><button type="button"${e.key === leadKey ? ' class="primary"' : ''} data-pick>Upload the ${esc(e.title)}</button>
          <input type="file" tabindex="-1" data-upload="${esc(e.key)}" accept=".xlsx,.csv,.tsv,.txt,.zip" aria-label="Upload the ${esc(e.title)}"></span></div>
        <div class="row-now"></div>
      </div>
    </div>`;
  }

  // a button that opens its file input: the input lies over it for the mouse, and the button takes the keyboard (Enter or
  // Space), so the chooser opens either way and the tab order holds one stop, not two
  function bindPick(span) {
    const b = span && span.querySelector('button'), inp = span && span.querySelector('input[type=file]');
    if (b && inp) b.onclick = () => inp.click();
  }

  async function loadExports() {
    try { EX = await apiCall('/api/ema/exports'); }
    catch (e) { $('#exports').innerHTML = `<div class="no small mt-3">${esc(e.message)}</div>`; return; }
    info.show_phi = EX.show_phi;
    // one filled button on the page: the export most overdue first (else the first), the others plain
    leadKey = ((EX.exports.find(e => rowFlag(e) === 'overdue') || EX.exports[0]) || {}).key;
    $('#exports').innerHTML = EX.exports.map(exportRow).join('');
    exportsNote(EX.exports);
    if (EX.mode === 'serverless') $('#dropHint').textContent = "EMA's reports and exports (Excel or CSV, any size), remittance (835) and claim (837) files and a .zip of those. Visit notes are uploaded on the practice's own server.";
    $('#exports').querySelectorAll('.export').forEach(bindExportRow);
  }

  // the line beside the checklist's title: how many rows need a pull (a report received and waiting to be read is not late)
  function exportsNote(rows) {
    const late = rows.filter(e => e.overdue).length, stuck = rows.filter(e => !e.overdue && e.stuck).length;
    $('#exportsNote').textContent = late ? `${late} of 5 overdue` : stuck ? `${stuck} upload${stuck === 1 ? '' : 's'} not read yet` : 'All five up to date';
  }

  // after an upload: each row's chip, last arrival and state again, leaving the rows (and the upload cards in them) in place
  async function refreshRows() {
    let ex;
    try { ex = await apiCall('/api/ema/exports'); } catch (e) { return; }
    EX = Object.assign(EX || {}, ex);
    ex.exports.forEach(e => {
      const el = document.querySelector(`.export[data-key="${e.key}"]`);
      if (!el) return;
      el.querySelector('.top .chip').outerHTML = chip(FLAG_CHIP[rowFlag(e)] || 'fresh');
      el.querySelector('[data-arrived]').innerHTML = arrivedHtml(e);
    });
    exportsNote(ex.exports);
  }

  function bindExportRow(el) {
    const key = el.dataset.key;
    const e = EX.exports.find(x => x.key === key);
    bindPick(el.querySelector('.attach'));
    el.querySelector('[data-upload]').onchange = ev => { const l = [...ev.target.files]; ev.target.value = ''; if (l.length) uploadAll(l, key, el.querySelector('.row-now')); };
    ['dragenter', 'dragover'].forEach(n => el.addEventListener(n, ev => { ev.preventDefault(); ev.stopPropagation(); el.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(n => el.addEventListener(n, ev => { ev.preventDefault(); ev.stopPropagation(); el.classList.remove('over'); }));
    el.addEventListener('drop', ev => { if (ev.dataTransfer.files.length) uploadAll([...ev.dataTransfer.files], key, el.querySelector('.row-now')); });
    el.querySelector('[data-edit]').onclick = () => editHowto(el, e);
    const hl = el.querySelector('[data-hist]');
    if (hl) hl.onclick = async ev => {
      ev.preventDefault();
      const box = el.querySelector('.hist');
      if (!box.hidden) { box.hidden = true; return; }
      try {
        const h = await apiCall(`/api/ema/exports/${key}/howto`);
        box.innerHTML = (h.history || []).map(v => `<div class="v"><div class="muted">${esc(when(v.at))}${v.name ? ' · ' + esc(v.name) : ''}${v.by ? ' (' + esc(v.by) + ')' : ''}</div>${esc(v.text) || '<i>empty</i>'}</div>`).join('');
        box.hidden = false;
      } catch (err) { box.textContent = err.message; box.hidden = false; }
    };
  }

  function editHowto(el, e) {
    const box = el.querySelector('.howto');
    const h = e.howto || {};
    let name = '';
    try { name = localStorage.getItem('remitwatch.howto.name') || ''; } catch (err) {}
    box.innerHTML = `<b class="small">How we pull this in our EMA</b>
      <div class="small muted" style="margin:4px 0">Write the steps the way the practice does them: where the report is in EMA, the filters, the columns, how it is saved. Anyone signed in can change it; earlier versions are kept.</div>
      <div class="theirs" data-theirs hidden></div>
      <textarea maxlength="4000" aria-label="How we pull the ${esc(e.title)} in our EMA">${esc(h.text || '')}</textarea>
      <div class="row"><input placeholder="Your name (optional)" maxlength="60" value="${esc(name)}" aria-label="Your name">
        <button class="primary" data-save>Save</button><button data-cancel>Cancel</button></div>
      <div class="small" data-msg></div>`;
    box.querySelector('textarea').focus();
    box.querySelector('[data-cancel]').onclick = () => loadExports();
    let base = h.at || '';
    box.querySelector('[data-save]').onclick = async () => {
      const text = box.querySelector('textarea').value, who = box.querySelector('input').value.trim();
      try { localStorage.setItem('remitwatch.howto.name', who); } catch (err) {}
      try {
        await apiCall(`/api/ema/exports/${e.key}/howto`, {method: 'PUT', body: JSON.stringify({text, name: who, base_at: base})});
        loadExports();
      } catch (err) {
        const msg = box.querySelector('[data-msg]');
        msg.className = 'small no';
        if (err.status === 409 && err.body && err.body.howto) {
          // someone saved the box after this person opened it: their text is shown above this person's draft, which is
          // kept; the next Save replaces theirs with what is in the box now (the earlier versions stay in the history)
          const t = err.body.howto;
          e.howto = t; base = t.at || '';
          const theirs = box.querySelector('[data-theirs]');
          theirs.innerHTML = `<div class="small"><b>Saved meanwhile${t.at ? ' ' + esc(when(t.at)) : ''}${t.name ? ' by ' + esc(t.name) : ''}${t.by ? ' (signed in as ' + esc(t.by) + ')' : ''}:</b></div>`
            + `<div class="text">${esc(t.text) || '<i>empty</i>'}</div>`;
          theirs.hidden = false;
          msg.textContent = 'Not saved: someone saved this box after you opened it. Their text is shown above, and yours is still in the box: '
            + 'add what is missing from theirs, then press Save again.';
        } else msg.textContent = 'Not saved: ' + err.message;
      }
    };
  }

  // ------------------------------------------------------------------------------------------- uploading files
  function newCard(file, box) {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `<div class="name"><span data-name>${esc(file.name)}</span> <span data-chip>${chip('checking')}</span></div>
      <div class="small muted" data-what></div><div class="bar"><div></div></div><div class="small" data-progress></div>
      <div class="row" data-buttons></div><div data-lines></div>`;
    (box || $('#now')).prepend(card);
    const set = (status, lines, progress) => {
      card.querySelector('[data-chip]').innerHTML = chip(status);
      if (lines !== undefined) card.querySelector('[data-lines]').innerHTML = linesHtml(lines);
      if (progress !== undefined) card.querySelector('[data-progress]').textContent = progress;
      card.dataset.status = status;
    };
    const bar = p => { card.querySelector('.bar>div').style.width = Math.round(Math.max(0, Math.min(1, p)) * 100) + '%'; };
    return {card, set, bar, buttons: card.querySelector('[data-buttons]'), what: card.querySelector('[data-what]')};
  }

  async function uploadAll(list, expected, box) {
    for (const file of list) {
      try { await uploadOne(file, expected, box); }
      catch (e) { console.error(e); }
    }
    refresh();
    refreshRows();
  }

  const titleOf = key => { const e = EX && EX.exports.find(x => x.key === key); return e ? e.title : key; };

  function missingLines(look) {
    const e = EX.exports.find(x => x.key === look.key);
    const cols = (look.missing || []).map(k => { const c = e && e.columns.find(x => x.key === k); return c ? `${c.column} (${c.why})` : k; });
    return cols;
  }

  async function uploadOne(file, expected, box) {
    const ui = newCard(file, box);
    if (!EX) await loadExports();
    if (!EX) { ui.set('stopped', ['The checklist could not be loaded, so the file was not checked. Reload the page.']); return; }
    const R = EX.recognition;
    let look;
    try { look = await lookAtFile(R, file); }
    catch (e) { look = {kind: 'unknown', unread: true}; }
    ui.card.dataset.kind = look.kind;
    if (look.key) ui.card.dataset.key = look.key;
    const serverless = EX.mode === 'serverless';
    // the wrong report, a missing column: said before anything is sent
    if (look.kind === 'old_excel') { ui.set('stopped', ['This is the old Excel format (.xls). Export it from EMA as Excel (.xlsx) or CSV.']); return; }
    if (look.twice) { ui.set('stopped', [`The column ${look.twice} appears twice in this ${titleOf(look.key)}. Export it again from EMA without adding or renaming columns.`]); return; }
    if (expected && look.key !== expected) {
      const said = look.key ? `This file is EMA's ${titleOf(look.key)}, not the ${titleOf(expected)}.`
        : `This file is not the ${titleOf(expected)}: its column names are not those of any of the five exports${look.columns && look.columns.length ? ' (it has ' + look.columns.slice(0, 8).join(', ') + ')' : ''}.`;
      ui.set('wrong', [said]);
      if (!look.key && look.kind !== 'unknown') return;
      const go = await choose(ui, look.key ? [`Send it as the ${titleOf(look.key)}`, 'Cancel'] : ['Send it anyway', 'Cancel']);
      if (go !== 0) { ui.set('stopped', [said, 'Not sent.']); return; }
    }
    if (look.kind === 'report' && look.missing.length) {
      ui.set('stopped', [`This is EMA's ${titleOf(look.key)}, but it lacks ${look.missing.length} column${look.missing.length === 1 ? '' : 's'} RemitWatch needs: ${missingLines(look).join('; ')}.`,
        'Run it again in EMA with every column, then upload that file. Nothing was sent.']);
      return;
    }
    const warn = look.kind === 'export' && look.missing.length ? [`This ${titleOf(look.key)} export lacks columns RemitWatch uses: ${missingLines(look).join('; ')}. It is sent anyway; run it with these columns next time.`] : [];
    if (look.key) ui.what.textContent = `Recognised as EMA's ${titleOf(look.key)}${look.fmt ? ' (' + (look.fmt === 'xlsx' ? 'Excel' : 'CSV') + ')' : ''}.`;
    const isNote = look.kind === 'note' || (look.kind === 'note_or_text' && !serverless);
    if (isNote && serverless) { ui.set('stopped', ["Visit notes are uploaded on the practice's own server, where appeals are built. Nothing was sent."]); return; }
    const chunked = !isNote && (serverless || look.kind === 'report' || file.size > CHUNK_FROM);
    if (!chunked) {                                            // the server's one-piece route, as before
      ui.set('sending', warn);
      send.onprogress = p => ui.bar(p);
      try { const r = await send(file); ui.card.querySelector('.bar').remove(); ui.set(r.status === 'zip' ? 'zip' : r.status, (r.lines || []).concat(warn)); ui.card.querySelector('[data-name]').textContent = r.name || file.name; }
      catch (e) { ui.set('failed', [e.message]); }
      send.onprogress = null;
      return;
    }
    await sendInPieces(file, look, ui, warn);
  }

  function choose(ui, labels) {
    return new Promise(resolve => {
      ui.buttons.innerHTML = labels.map((l, i) => `<button ${i === 0 ? 'class="primary"' : ''} data-i="${i}">${esc(l)}</button>`).join('');
      ui.buttons.querySelectorAll('button').forEach(b => b.onclick = () => { ui.buttons.innerHTML = ''; resolve(Number(b.dataset.i)); });
    });
  }

  // a gate the sending loop waits at between pieces: Pause closes it, Resume opens it
  function makeGate() {
    let open = true, waiters = [];
    return {
      pass: () => open ? Promise.resolve() : new Promise(r => waiters.push(r)),
      pause: () => { open = false; },
      resume: () => { open = true; const w = waiters; waiters = []; w.forEach(r => r()); },
      get paused() { return !open; },
    };
  }

  class Rewind extends Error { constructor(index) { super('rewind'); this.index = index; } }
  class Fresh extends Error {}            // this upload cannot go on (its pieces were made by another browser, or are lost): send the file anew
  class Stop extends Error {}             // stopped, and the card already says why

  async function sendInPieces(file, look, ui, warn) {
    const gz = !!(look.text && look.key && typeof CompressionStream !== 'undefined');
    const stop = lines => { ui.buttons.innerHTML = ''; ui.set('stopped', lines.concat(warn)); };
    const already = s => { ui.buttons.innerHTML = ''; ui.bar(1); ui.set('already', (s.lines || []).concat(warn), ''); ui.card.dataset.done = '1'; };
    // a file this site's run could never read (Vercel reads at most cloud.MAX_FILE_BYTES of a file as it is sent) is not
    // sent at all; one compressed on the way is checked by the server as its pieces arrive
    if (!gz && EX && EX.read_max && file.size > EX.read_max) { stop([EX.read_max_line || 'This file is too big to read on this site.', 'Nothing was sent.']); return; }
    const gate = makeGate();
    const t0 = performance.now();
    const times = {};
    const pauseBtn = () => {
      ui.buttons.innerHTML = `<button data-pause>${gate.paused ? 'Resume' : 'Pause'}</button>`;
      ui.buttons.querySelector('[data-pause]').onclick = () => { if (gate.paused) { gate.resume(); ui.set(ui.card.dataset.was || 'sending'); } else { gate.pause(); ui.card.dataset.was = ui.card.dataset.status; ui.set('paused', undefined, 'Paused. Press Resume to carry on.'); } pauseBtn(); };
    };
    pauseBtn();
    // 1. the file's own checksum
    ui.set('hashing', warn, 'Working out the checksum of the file…');
    const sha = await sha256File(file, n => { ui.bar(n / file.size); ui.card.querySelector('[data-progress]').textContent = `Checking the file: ${mb(n)} of ${mb(file.size)}`; }, gate.pass);
    times.hash_ms = Math.round(performance.now() - t0);
    ui.card.dataset.sha256 = sha;
    // 2. start, or carry on, or "already have this"
    const startBody = extra => JSON.stringify(Object.assign({name: file.name, size: file.size, gz, report_hint: look.hint || null, sha256: sha}, extra || {}));
    let st;
    try { st = await apiCall('/api/uploads/start', {method: 'POST', body: startBody()}); }
    catch (e) { stop([e.message]); return; }
    if (st.status === 'already') { already(st); return; }
    let uid = st.upload_id;
    ui.card.dataset.uploadId = uid;
    let next = st.next_index || 0;
    let serverChunks = st.chunks || [];
    const what = ui.what.textContent;
    if (st.status === 'resume') ui.what.textContent = (what + ` Carrying on: ${next} piece${next === 1 ? '' : 's'} had arrived before.`).trim();
    // 3. the pieces, then 4. the server checks the whole file and hands it to the cycle. The server's own count of what
    // arrived decides each time: pieces it lacks are sent, and an upload it cannot carry on is sent anew (at most twice)
    let sentBytes = 0, pieces = 0, wire = 0, retries = 0, fresh = 0, fin = null;
    for (let round = 0; round < 12 && !fin; round++) {
      try {
        ({sentBytes, pieces, wire} = await pump());
        times.send_ms = Math.round(performance.now() - t0 - times.hash_ms);
        fin = await finish();
        if (!fin) await reload();
      } catch (e) {
        if (e instanceof Stop) return;
        if (e instanceof Rewind) { await reload(); continue; }
        if (e instanceof Fresh && fresh < 2) {
          fresh++;
          let s;
          try { s = await apiCall('/api/uploads/start', {method: 'POST', body: startBody({new: true})}); }
          catch (err) { stop([err.message]); return; }
          if (s.status === 'already') { already(s); return; }
          uid = s.upload_id; ui.card.dataset.uploadId = uid; next = s.next_index || 0; serverChunks = s.chunks || [];
          ui.what.textContent = (what + ' Sending it from the start: the upload of it that was cut off could not be carried on'
            + (e.message ? ` (${e.message.replace(/[.\s]+$/, '')})` : '') + '.').trim();
          pauseBtn();
          continue;
        }
        stop([e.message || String(e)]);
        return;
      }
    }
    if (!fin) { stop(['The upload could not be finished just now. Choose the same file again to carry on.']); return; }
    times.total_ms = Math.round(performance.now() - t0);
    ui.card.dataset.times = JSON.stringify(Object.assign(times, {pieces, sent_bytes: sentBytes, size: file.size, gz, retries, fresh}));
    ui.card.dataset.done = '1';
    showResult(ui, fin, warn);
    if (fin.state === 'finished' && !fin.applied) follow(ui, uid, warn);

    // what the server holds of this upload now: the pieces to compare with, and the first one it lacks
    async function reload() {
      const s = await apiCall(`/api/uploads/${uid}/status`).catch(() => null);
      if (s && s.state === 'receiving') { next = s.next_index; serverChunks = s.chunks || []; }
    }

    // the finish: the upload as the server now describes it, or null when it lacks pieces (sent next round)
    async function finish() {
      ui.buttons.innerHTML = '';
      ui.set('verifying', warn, `Sent ${pieces} piece${pieces === 1 ? '' : 's'} (${mb(sentBytes)}${gz ? ', compressed from ' + mb(file.size) : ''}). The server is checking the whole file…`);
      for (let attempt = 0; ; attempt++) {
        try { return await apiCall(`/api/uploads/${uid}/finish`, {method: 'POST', body: JSON.stringify({sha256: sha, chunks: pieces, bytes: sentBytes})}); }
        catch (e) {
          if (e.status === 422) { ui.set('refused', [(e.body && e.body.detail) || e.message].concat(warn)); throw new Stop(); }
          if (e.body && e.body.restart) throw new Fresh();
          if (e.status === 409) {
            // the server counts what arrived otherwise than this page: its count decides
            const s = await apiCall(`/api/uploads/${uid}/status`).catch(() => null);
            if (s && s.state && s.state !== 'receiving') return s;              // finished already: an answer that was lost
            if (s && s.received < pieces) return null;                          // it lacks pieces: they are sent again
            if (s && s.received === pieces && s.bytes !== sentBytes && attempt < 3) { sentBytes = s.bytes; continue; }
          }
          if (e.status && e.status < 500 && e.status !== 409) { stop([e.message]); throw new Stop(); }
          if (attempt >= 3) { stop([e.message, 'Choose the same file again to carry on.']); throw new Stop(); }
          await sleep(2000 * (attempt + 1));
        }
      }
    }

    async function pump() {
      let index = 0, sent = 0, read = 0, wireBytes = 0;
      // a plain file is cut where the server stopped (every piece before the last is exactly CHUNK long, so that is
      // next * CHUNK, never past the file's end); a compressed one is made again and compared piece by piece
      const plainFrom = gz ? 0 : Math.min(next * CHUNK, file.size);
      if (!gz) { index = next; sent = plainFrom; read = plainFrom; }
      const total = gz ? 0 : Math.ceil(file.size / CHUNK);
      ui.set('sending', warn);
      for await (const {data, last} of cutPieces(sourceStream(file, gz, plainFrom, n => { read += n; }))) {
        const hex = sha256Hex(data);
        if (index < next) {                                    // on the server already: the same bytes, or the file is sent anew
          if (serverChunks[index] !== hex) throw new Fresh('its pieces were compressed by another browser or computer');
          index++; sent += data.length; continue;
        }
        await gate.pass();
        await sendOne(index, data, hex, last ? index + 1 : total);
        index++; sent += data.length; wireBytes += data.length;
        ui.bar(read / file.size);
        ui.card.querySelector('[data-progress]').textContent = `Sending: ${Math.round(100 * read / file.size)}% of ${mb(file.size)} read, ${mb(sent)} sent${gz ? ' (compressed)' : ''}.`;
      }
      return {sentBytes: sent, pieces: index, wire: wireBytes};
    }

    async function sendOne(index, data, hex, total) {
      for (let attempt = 0; ; attempt++) {
        let r = null, j = null;
        try {
          r = await fetch(`/api/uploads/chunk?upload_id=${uid}&index=${index}&total=${total}&sha256=${hex}`,
            {method: 'POST', headers: Object.assign({'Content-Type': 'application/octet-stream'}, XHR), body: data, credentials: 'same-origin', cache: 'no-store'});
          try { j = await r.json(); } catch (e) {}
        } catch (e) { r = null; }
        if (r && r.ok) return j;
        if (r && j && j.restart) throw new Fresh();           // the upload cannot go on: the file is sent anew
        if (r && r.status === 409 && j && j.next_index != null) {
          if (j.next_index > index) return j;                  // the server had it already: an answer that was lost
          if (j.next_index < index) throw new Rewind(j.next_index);
        }
        if (r && r.status !== 422 && r.status < 500 && r.status !== 429 && r.status !== 409) throw new Error((j && j.detail) || `The server answered ${r.status}.`);
        retries++;
        if (attempt >= 4) {                                    // the connection is gone: wait for the person
          gate.pause();
          ui.card.dataset.was = 'sending';
          ui.set('paused', undefined, `Paused at piece ${index + 1}: ${(j && j.detail) || 'the connection failed'}. Press Resume to carry on from there.`);
          pauseBtn();
          await gate.pass();
          ui.set('sending');
          attempt = -1;
          continue;
        }
        await sleep(1000 * 2 ** attempt);
      }
    }
  }

  function showResult(ui, r, warn) {
    const st = uploadChip(r);
    const lines = (r.lines || []).concat((r.checks || []).map(c => c.message)).concat(warn || []);
    ui.bar(1);
    ui.set(st, lines, '');
    ui.card.dataset.result = st;
  }

  // after the hand-off: look again until the cycle has read the file (every 15 s for 30 minutes)
  async function follow(ui, uid, warn) {
    for (let i = 0; i < 120; i++) {
      await sleep(15000);
      let s;
      try { s = await apiCall(`/api/uploads/${uid}/status`); } catch (e) { continue; }
      showResult(ui, s, warn);
      if (s.applied || s.state !== 'finished') { refreshRows(); return; }
    }
  }

  // ------------------------------------------------------------------------------- the person's recent uploads
  async function recent() {
    let r;
    try { r = await apiCall('/api/uploads/recent'); } catch (e) { return; }
    const items = r.items || [];
    $('#recentPanel').hidden = !items.length;
    $('#recent').innerHTML = items.map(u => {
      const st = uploadChip(u);
      return `<div class="card"><div class="name">${esc(u.name)} ${chip(st)} <span class="muted small" style="font-weight:400">${esc(when(u.created_at))}</span></div>
        ${linesHtml((u.lines || []).concat((u.checks || []).map(c => c.message)))}</div>`;
    }).join('');
  }

  // ---------------------------------------------------------------- visit notes and the server's one-piece uploads
  function noteTools(n) {
    if (!n) return '';
    const pick = (n.candidates || []).map((c, i) => `<button data-pick="${i}" data-note="${esc(n.note_id)}">Visit of ${esc(day(c.service_date))}${c.patient ? ' · ' + esc(c.patient) : ''} · claim ${esc((c.claims || []).join(', '))}${(c.payers || []).length ? ' · ' + esc(c.payers.join(', ')) : ''}</button>`).join(' ');
    return `<div class="row">${n.status === 'choose' ? pick : ''}<a href="/api/uploads/notes/${esc(n.note_id)}/file" target="_blank" rel="noopener">View the note</a>
      <button data-remove="${esc(n.note_id)}" class="small">Take it back</button></div>`;
  }

  function bindNoteTools(el) {
    el.querySelectorAll('[data-pick]').forEach(b => b.onclick = async () => {
      try { const r = await apiCall(`/api/uploads/notes/${b.dataset.note}/match`, {method: 'POST', body: JSON.stringify({pick: Number(b.dataset.pick)})});
        b.closest('.card').querySelector('ul').innerHTML = (r.lines || []).map(l => `<li>${esc(l)}</li>`).join(''); b.parentNode.querySelectorAll('[data-pick]').forEach(x => x.remove()); refresh(); }
      catch (e) { RWShell.say('Not saved: ' + e.message, {error: true, stay: true}); }
    });
    el.querySelectorAll('[data-remove]').forEach(b => b.onclick = async () => {
      if (!(await RWShell.ask(b, 'Take this note back? Its file is deleted from the server. A packet built from it is not faxed from now on. The next appeal builds it again from the note in its place.', {yes: 'Yes, take it back'}))) return;
      try { await apiCall(`/api/uploads/notes/${b.dataset.remove}/remove`, {method: 'POST', body: '{}'}); b.closest('.card').remove(); refresh(); }
      catch (e) { RWShell.say('Not removed: ' + e.message, {error: true, stay: true}); }
    });
  }

  async function refresh() {
    recent();
    let h;
    try { h = await apiCall('/api/uploads'); } catch (e) { $('#hist').textContent = e.message; return; }
    if (!h.server) {
      $('#away').hidden = false; $('#awayMsg').textContent = h.message; $('#here').hidden = true;
      const c0 = h.cycle || {};          // the Vercel site analyses only on a press (the owner's decision 1)
      if (c0.press_only) {
        const an = c0.analysed || {};
        $('#pressLine').hidden = false;
        $('#pressLine').textContent = (c0.text || '') + ' ' + (an.at ? 'It last analysed ' + when(an.at) + '.' : 'It has not analysed yet.')
          + (c0.running ? ' A run is going now.' : '') + (c0.unchecked && c0.unchecked.text ? ' ' + c0.unchecked.text : '');
      }
      return;
    }
    info = Object.assign(info, h); $('#here').hidden = false;
    const c = h.cycle || {};
    $('#cycle').innerHTML = (h.queued ? `<b>${h.queued} file${h.queued === 1 ? '' : 's'}</b> to be read by the next cycle. ` : 'Nothing is waiting. ')
      + (c.running ? `A cycle is running (since ${esc(when(c.since))}).` : c.last_finished ? `The last cycle finished ${esc(when(c.last_finished))}${c.last_error ? ' with an error: ' + esc(c.last_error) : ''}.` : '')
      + (c.next && !c.running && h.queued ? ` The next starts by ${esc(when(c.next))}, sooner after an upload.` : '');
    $('#runNow').disabled = !h.queued || c.running;
    const items = h.items || [];
    $('#hist').innerHTML = items.length ? items.map(u => `<div class="card"><div class="name">${esc(u.name)} ${chip(u.status === 'done' && u.note ? u.note.status : u.status)}
        <span class="muted small" style="font-weight:400">${esc(when(u.uploaded_at))}${u.uploaded_by ? ' by ' + esc(u.uploaded_by) : ''}${u.parent ? ' · from a zip' : ''}</span></div>${linesHtml(u.lines)}${u.status === 'removed' ? '' : noteTools(u.note)}</div>`).join('')
      : '<div class="muted">Nothing uploaded yet.</div>';
    bindNoteTools($('#hist'));
    clearTimeout(refresh.t);                       // one wait at a time, however often the page asks again
    refresh.t = h.queued ? setTimeout(refresh, 15000) : null;
    needed();
  }

  async function needed() {
    let n;
    try { n = await apiCall('/api/uploads/needed?limit=100'); } catch (e) { $('#needed').textContent = e.message; return; }
    if (!n.items.length) { $('#needed').innerHTML = '<div class="muted">None: every open item that rests on the visit note has its packet.</div>'; return; }
    const phi = info.show_phi;
    $('#needed').innerHTML = `<div class="muted" style="margin-bottom:4px">${n.total} item${n.total === 1 ? '' : 's'}; an uploaded note covers ${n.covered}.${n.total > n.items.length ? ' The first ' + n.items.length + ' are shown.' : ''}</div>
      <div style="overflow-x:auto"><table><thead><tr><th>Closes</th><th>Visit</th><th>Claim ID</th>${phi ? '<th>Patient</th>' : ''}<th class="hide-sm">Payer</th><th class="hide-sm">Code</th><th class="num">Amount</th><th>Note</th></tr></thead>
      <tbody>${n.items.map(i => `<tr><td>${esc(day(i.appeal_deadline)) || '<span class="muted">unknown</span>'}</td><td>${esc(day(i.service_date))}</td><td>${esc(i.claim_id || '')}</td>${phi ? `<td>${esc(i.patient || '')}</td>` : ''}
        <td class="hide-sm">${esc(i.payer_group || '')}</td><td class="hide-sm">${esc(i.code || '')}</td><td class="num">${money(i.cents)}</td>
        <td>${i.note === 'found' ? '<span class="ok">uploaded</span>' : i.note === 'ambiguous' ? '<span class="warn">uploaded: confirm or pick it in the item</span>'
          : `<span class="attach"><button type="button" class="small">Attach note</button><input type="file" tabindex="-1" data-fid="${esc(i.finding_id)}" accept=".pdf,.docx,.txt,.rtf,.html,.htm" aria-label="Attach the visit note for claim ${esc(i.claim_id || '')}"></span>`}</td></tr>`).join('')}</tbody></table></div>`;
    $('#needed').querySelectorAll('.attach').forEach(bindPick);
    $('#needed').querySelectorAll('input[data-fid]').forEach(inp => inp.onchange = async () => {
      const f = inp.files[0]; if (!f) return;
      const cell = inp.closest('td'); cell.textContent = 'sending…';
      try { const r = await send(f, inp.dataset.fid); cell.innerHTML = r.status === 'rejected' ? `<span class="no">${esc(r.reason)}</span>` : '<span class="ok">uploaded</span>'; refresh(); }
      catch (e) { cell.innerHTML = `<span class="no">${esc(e.message)}</span>`; }
    });
  }

  function init() {
    const drop = $('#drop');
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => { if (e.dataTransfer.files.length) uploadAll([...e.dataTransfer.files], null, $('#now')); });
    $('#files').onchange = () => { const l = [...$('#files').files]; $('#files').value = ''; if (l.length) uploadAll(l, null, $('#now')); };
    $('#runNow').onclick = async () => { $('#runNow').disabled = true; try { await apiCall('/api/sync/run', {method: 'POST', body: '{}'}); $('#cycle').textContent = 'Started: the files are read within about a minute.'; setTimeout(refresh, 20000); } catch (e) { $('#cycle').textContent = e.message; } };
    loadExports();
    refresh();
  }
  init();
})(typeof window !== 'undefined' ? window : globalThis);
