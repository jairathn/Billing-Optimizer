# UI release fix round: progress (the one review of wip/ui-release, findings d-1 to d-15 and e-1 to e-4)

Branch `wip/ui-release` (worked on the local branch `ui-rel-fix` tracking it), worktree `$S/tk/wt/ui-rel-fix`, base
233c24f. Demo on port 8771, private folder `$S/tk/units/ui-rel-fix`. Invented data only.

The review walked the merged UI (ui-a, ui-panel, ui-pages, ui-work, ui-words) as a designer and a biller (d-*) and as an
engineer for privacy and both hosts (e-*). Each finding is checked against the branch first; the verified highs and
mediums are fixed worst first, with a test that fails before the fix (broken on purpose, then restored); the lows are
fixed when the change is one safe line, else they wait with the reason.

## Findings

| id | severity | verified | state |
|----|----------|----------|-------|
| e-2 | high | yes: a one-word name the code uses (Grace, Mark, Pat, An) is never learned | fixed |
| d-1 | high | yes: a packet from another patient's note downloaded (200) and emailed with no warning | fixed |
| e-1 | high | yes: the reviewer's test fails on both hosts at 233c24f | fixed |
| d-2 | high | yes: Today's lost (insights.missed) and paid back (Paid marks) were second counts of Results' figures | fixed |
| d-3 | high | yes: 40 alert/confirm/prompt calls (the reviewer's script) | fixed: 0 left |
| d-4 | high | yes: ModMed first after the Checklist, red "fix the first x" with nothing set | fixed |
| e-3 | medium | yes: the reviewer's node check exits 1 | fixed |
| d-5 | medium | yes in code: Start called goWork (the list only) | fixed |
| d-9 | medium | yes in code: closeDetail focused the row that opened the panel | fixed |
| d-13 | low | yes in code: no toast; Undo toast 8 s, Undo 10 s | fixed |
| d-7 | medium | yes: 5 of 10 headlines over 20 words on the invented world | fixed (headlines); X12 words on the card fixed |
| d-8 | medium | yes: docked panel 36vw, Payer column kept, phone buttons 28 px; the bar wrapped to 3-4 lines (48 to 132 px) when the panel opened | fixed |
| d-6 | medium | yes in code: upload_note opened /upload in a new tab; #drop at 2,100 px of 6,193 | fixed |
| d-14 | low | yes in code | fixed (checks.js) |
| d-11 | low | yes | fixed ("changed today", the filing limit's words, "Open the batch", no stray dot before Hidden) |
| e-4 | low | yes: no doc said the chosen name is not a sign-in | fixed |
| d-10 | low | yes | fixed in part (the panel's filing date); the rest waits |
| d-12 | low | yes | waits |
| d-15 | low | yes | fixed (HANDOFF wait list) |

## Log

- e-2 (staff name in the decision log and the bundle). Verified: tests/test_staff_names_private.py's new cases fail at
  233c24f (7 of 8: the scrubber leaves "Grace", "Mark", "Pat" and "An" whole; the server and serverless decision logs
  keep "Marked Paid by Grace"). Fix: `runlog.Scrubber.people_exact` keeps a one-word staff name that the code also uses
  (or a short English word) as written, with its capital, and masks it where it is written so (the two-letter path too);
  `cloud.run_cloud_cycle` learns the journal's actors before the fold, so a warning logged while folding is masked (a
  test makes the fold fail with the name in its error). Broken on purpose: without people_exact 7 fail; without the
  cloud line the serverless case fails. Restored (git diff clean against the fix).
- d-1 (another patient's note in the panel's packet). Verified: a build from an invented note of another patient
  answered 200 with no warning, its PDF downloaded (200) and the note was kept as the visit's own. Fix: one rule for
  every way a packet goes out (`api._note_hold`, the rule the fax send already used): `appeals_build` works the note's
  identity out once, answers `note_hold` (why, visit date) when the note's text does not show this visit, and keeps the
  note as the visit's own only when it does; GET `/api/packets/{id}/pdf` and POST `/email` answer 409 "Check the visit
  note before it goes" until a person ticks; `?check=1` shows the PDF in the browser for that look (never a download);
  POST `/api/packets/{id}/note-checked` keeps the person's check with the packet (both hosts: `cloud.mark_packet_checked`)
  and a line in the item's history; the fax send takes the kept check as the tick, and a tick given at the fax is kept
  with the packet too. The panel: one blocking notice in place of Download, Copy and Send, with "Open it", the tick and
  "Use this note"; earlier packets, the route card's Open/Print the packet and the fax step wait for the same tick.
  Tests (tests/test_hotfix2.py, both hosts): the wrong note is held everywhere until the tick, then goes, the fax with
  no second tick; the right note downloads at once. Moved with the same guarantee: test_h6_a_scanned_note (the scan's
  own packet still never goes without the tick; a send naming no packet now builds from EMA's own note, since the scan
  is no longer kept as the visit's note) and test_identifiers_stay_on_the_server (a person ticks before the download).
  Broken on purpose (no hold): 3 fail; restored.
- e-1 (one definition of the last day). Verified with the reviewer's test (tests/test_last_day.py): at 233c24f the
  brief counted a last-chance item lost while Work's missed list did not hold it, and Results' at-risk list left it out,
  on both hosts. Fix: `states.last_day` and `states.last_day_sql` (our target until it passes, then the payer's own
  limit while still ahead) used by `api._last_day`, `results.live_sql` (at risk, its order, given up recoverable and
  past), `insights.missed` and `insights.work` (a last-chance item counts inside its window, dated by the limit).
  Broken on purpose (insights and results back): both host cases fail; restored.
- d-2 (one definition of the scoreboard figures). Verified in the code: Today's strip read `brief.missed` (open items
  whose window closed in 30 days) and `brief.recovered.year_cents` (Paid marks), Results reads `results.scoreboard`.
  Fix: `/api/brief` carries `score_week`, the scoreboard's current week (`api._score_week`: results.scoreboard itself);
  Today's strip shows "Lost to deadlines, this week" and "... came back after flagging" from it, with the scoreboard's
  words ("windows closed, unworked"), the figure opens Results; Results' This week says "windows closed, unworked" too;
  "moved on today" reads "changed today" (d-11). `brief.missed` stays only as the count of the Work list "Window closed
  in the last 30 days" (the e-1 test holds them equal). Tests: tests/test_one_definition.py (both hosts: score_week is
  the scoreboard's current week; the strip reads no second count). Moved with the same guarantee:
  test_ema_pages2::test_today_s_strip_adds_up (the lost figure is now the scoreboard's week). Broken on purpose: 3 fail.
- d-3 (browser boxes). RWShell.say (a toast), RWShell.ask (a question beside the button, a second button answers it;
  Cancel or Esc is a no) and RWShell.askText (a reason field with the six reason chips; an empty answer is refused
  with its sentence) in shell.js, loaded first on every page; all 40 calls moved (desk.js bulk bar, Let go, Mark sent,
  fax, export, fee letter, hide; faxes.js read-backs and confirmations; data.js, upload.js, index.html, swing.js,
  ops.js, policy.js; today.js's second Let go question could never be asked and is gone). tests/test_style.py counts the
  calls (BROWSER_BOXES_MAX = 0, only lowered) and checks the shell offers the three; plans/tools/ui/checks.js --bulk
  ticks two rows and presses every bulk button with the dialog handler counting. Harnesses moved with the same
  guarantee (the questions recorded as the boxes were): test_desk.py, test_ema_pages.py, test_ema_pages2.py,
  hotfix2_page.js (an inline question answered yes as the confirm was). Broken on purpose: both style tests fail.
- d-4: Setup's panel is "ModMed EMA connection (optional)", after the fax line, steps and Test folded; with nothing set
  the Test says "Not set up. Notes are attached by hand." in quiet words (modmed.check answers configured: false);
  Upload opens "EMA's reports and visit notes come in by hand". tests/test_shell.py; broken on purpose: both fail.
- e-3: in a one-payer list the who cell carries the payer's can't-be-checked mark and the "by <name>" line;
  tests/one_payer_row_check.js (the reviewer's check) runs from tests/test_desk.py; broken on purpose: exit 1.
- d-5: Today's Start draws the step's list and opens its first item ("Step 1 · Item 1 of N"); the step title still
  opens the list; the label clears when a list is drawn again. d-9: Esc focuses the closed item's row (or the row now
  in its place), so j goes on from there. d-13: "I asked the payer" shows a toast and moves to the next item; an Undo
  toast lives the 10 s Undo works.
- d-7: story._headline cuts a first sentence over 20 words at its first clause (a state's label stays with it); the
  panel shows the headline and the rest of the step leads its fold; route card fields that name X12 loops say EMA's
  words (routes.ema_words). tests/test_headlines.py (every verdict and step headline on the invented world: 5 of 10
  over 20 words before, 0 after), tests/test_routes.py. Moved with the same guarantee: test_story's plain() (every word
  of the sentence, in order, nothing added; punctuation and case of the cut aside).
- d-8: from 1280 to 1535 px the docked panel is 440 px and the list drops its Payer column (the payer rides in the
  patient cell's second line); on a phone every panel control is 44 px tall at least and the next step's main button
  sits in a bar pinned to the sheet's foot with its own Next. Waits: the toolbar's Filters and Export menu and the
  route card's fold after three fields (a menu and a fold of their own, measured first).
- d-6: the panel's "Upload the note" opens the appeal step's fold and focuses its file field. Waits: the Upload page's
  order (drop zone first) and a panel redrawn when a note arrives on another tab.
- e-4 (ab6272f): README.md and docs/OPERATIONS.md say who is working is a label each person picks, not a sign-in, and
  name the X-RemitWatch-Person header; the person card says "This is not a sign-in: anyone using this computer can
  change it." tests/test_docs.py checks both.

### Second session (resumed at ab6272f, 2026-11-28)

- d-8, the rest. Measured on the demo (port 8771) with the panel docked: the Work bar went from 48 to 132 px (three
  lines at 1400x900, four at 1366x650, three at 1536x730), so the list jumped 84 px down when an item opened. Now, with
  the panel docked (1280 px and up), Kind of work and Order move into the Filters fold (as on a phone), Export too (a
  second button there, #wlCsvDock, bound to the same export), Start goes while an item is open: the bar stays 48 px at
  all three sizes. On a phone the route card shows its first three fields and folds the rest behind "All N fields"
  (it opens by itself when the next field to paste is folded); the claim's copy button, the card's own buttons and the
  fold's button are 44 px tall. checks.js --panel now measures the bar's height before and after, What happened's
  characters beside the docked panel (12 or more) and the phone panel's buttons under 44 px (it found 3; now 0).
- d-6, the rest. Upload: the drop zone comes first under the title (#drop 2,100 to 273 px), each export keeps its
  title, purpose, last arrival, the practice's steps and its button, with filters, columns and format in a closed fold
  (page 6,193 to 5,607 px); the notes appeals wait for lead the server's lists (2,393 to 1,688 px). An open panel whose
  step waits for its note asks again when its tab is shown again (visibilitychange; never window focus, which a file
  chooser closing also fires). The panel's own "Upload the note" was fixed in the first session; on the demo no Work
  item in the first 80 leads with it (build_packet leads), so it was checked in code.
- Lows: d-11 "Open the batch" on Today's batches (13 buttons named Start before) and no stray " · " before Hidden on
  Work lists; d-10 the panel's filing date in the practice's long form; d-15 the wait list in plans/HANDOFF.md.
- Tests: tests/test_shell.py (the Upload order and fold, the redraw on visibilitychange, the docked bar and the phone
  card). Broken on purpose (the static changes stashed): all three fail; restored (the diff compared byte for byte).
- Waits, with the reason: d-12 (Results' repeated tiles) removes a section that test_results and the Results walk read,
  and needs its own measured change; d-10's other parts (insights and alerts text, faxTime's fallback, day()'s year)
  each move a server sentence or test_practice_day's expectation and are not one line each.
- d-5 and d-9, measured: checks.js --walk (new) presses Today's Start (the step's first item opens in 227 to 369 ms,
  "Step 1 · Item 1 of 9") and, in Work, opens a row with o, walks on with j j inside the panel, then Esc and j: the focus
  is on the row of the item just worked, then the row after it. Broken on purpose: without closeDetail's land(rowFor())
  the focus went back to the row that opened the panel (row 2 for row 4); without Today's openDetail no item opened.
  Both restored (git diff empty).
- The UI checks (plans/tools/ui/checks.js --panel --bulk) on all 22 pages at 1366x650, 1536x730, 1400x900 and 390x844,
  light and dark (176 screens): the drop zone, now on Upload's first screen, showed its "choose files" words at 3.31:1
  in dark (an inline accent color); it takes a link's look from rw.css now (.drop .pick) and the inline style count goes
  from 184 to 183 (STYLE_ATTRS_MAX lowered). Then: 0 sideways overflow, 0 script errors, 0 browser dialogs (the bulk
  step pressed five bulk buttons), at most 6 font sizes, at most 3 accent elements, at most 1 filled button, 0 contrast
  failures; Work's first row at 215 px (1400x900), 7 rows at 1366x650, 9 at 1536x730, 12 at 1400x900, 9 cards at
  390x844; 0 px row change and 0 px bar change when the panel opens; What happened keeps 13 to 27 characters beside the
  docked panel; 0 panel buttons under 44 px on a phone's first screen.
- claude/ecstatic-fermi-orfcgo moved by aeb4bb6 (the same HANDOFF tree as 17b2e24): merged, nothing to resolve.
