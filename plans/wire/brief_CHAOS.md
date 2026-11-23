# Chaos testing: the tester is an irrational human (the owner, 2026-11-23: "Make sure you're creative with your bug hunt. Your
# tester should adopt the mentality of an irrational human")

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad. Read ${S}/wire/PLAN.md (the owner's
words: people will stop double-checking; one wrong "worked" or "resolved" ends trust; no silent money loss; easy for a newcomer).

## The mindset
You are not a QA engineer following a script. You are a real person at a busy practice, tired, interrupted, impatient, a little
careless, sometimes suspicious of the software, sometimes trusting it far too much. You do things in the wrong order, twice, from
two places at once, with the wrong file, and you change your mind. You never read instructions; you skim. You click the biggest
button. You paste whatever is on your clipboard. You leave things half done and come back tomorrow. When something looks off you
poke at it harder. Every bug you find is a way RemitWatch could lose money, fax the wrong thing, hide work, leak a patient's
details, or make a person stop trusting it.

## Personas (each tester plays one or two, fully, and invents more behaviours in the same spirit)
1. The impatient clicker: double and triple clicks every button (Appeal by fax, Mark sent, Let go, Upload, Save number, Accept
   pattern, Apply/Discard a held report); clicks before the page finishes loading; hammers refresh; presses Back in the middle of a
   send; closes the tab right after "Appeal everything appealable"; clicks again after a slow answer. Look for: two faxes for one
   item, two events, a status set twice, a half-done state, a send that went out but shows as failed (or the reverse).
2. The confused newcomer: uploads the wrong things (a PDF renamed .csv, a phone photo, an 835 into the EMA uploader, the weekly
   export as if it were Production, a password-protected workbook, an Excel CSV with a BOM, semicolons or Windows-1252 accents, the
   same report renamed "final_FINAL (2).csv", a zip inside a zip, an empty file, a file of only a header); searches Find a claim
   with spaces, dashes, lower case, a patient name spelled wrong, a member id with a leading zero dropped; follows the screen's
   words literally; marks things Paid because "it looked paid"; lets go with the reason "idk"; accepts every pattern on the policy
   page because the button is there. Look for: anything read wrongly without a plain refusal, any count that lies, any step that a
   literal reader would get wrong, any wording that misleads.
3. The distracted multitasker: two tabs and a phone on the same item at once (one tab faxes the appeal while the other lets it go;
   both mark it sent; one edits the payer's fax number while the other sends); leaves the page open overnight and acts on an item
   the nightly run has closed or reopened; the login expires in the middle of an action; the laptop sleeps during an upload; the
   network drops and comes back; browser zoom 200%, a 320 px phone, landscape, dark mode. Look for: stale screens that let a person
   act on an item that changed, lost work, duplicate sends, a page that breaks at small widths.
4. The skeptic and flip-flopper: changes statuses back and forth (open, sent, let go, reopen, paid, reopen) fast; reopens what the
   software closed; answers spot checks "already worked" when it was not, then the reverse; flips the owner's policy accept/pursue
   on the same pattern several times; types amounts as "1,000.00", "$1k", "-50", "0", "1e9", "12.345"; dates as 02/30/2026,
   13/01/2026, 1900-01-01, 2099-12-31, "yesterday"; puts HTML, a script tag, emoji, right-to-left text, a 50,000-character note, or a
   patient's name and date of birth into a free-text reason. Look for: history that no longer explains the item, money counted twice
   on the Results tab, an injection that runs, a reason that leaks into logs or the diagnostic bundle, a crash.
5. The power user who breaks things on purpose without meaning harm: mashes keyboard shortcuts; opens fifty drawers in a row;
   edits the address bar (#item=../../x, #item=<script>, a finding id from another practice, page=-1, limit=1000000, unknown
   filters, sort by a column that does not exist); calls the API directly with missing fields, wrong types, huge strings, null bytes,
   repeated requests (the same POST five times), and without the X-Requested-With header; downloads every CSV with the names switch
   flipped in the middle. Look for: 500s, data in an answer that the names switch should hide, any route that works without the
   login, any action that is not idempotent where it must be.
6. The accidental saboteur of the data (Dropbox): drops the same Production report re-exported with columns in another order, an
   A/R with an extra column, a report covering 2019, a Production file whose payer names are all blank, a file deleted and put back,
   moved between folders, renamed after it was read, modified while a run reads it; an 835 that is only a PLB, the same 835 again
   with a new control number, a cut-short 835, a giant zip. Look for: money doubled or lost, a file silently skipped, a run that
   stops, a held file nobody is told about.
7. The owner who changes their mind: flips settings between runs (bulk appeal on/off, which EMA reports a host reads), changes or
   deletes a payer's fax number while a batch is going, sets the ROI page's minutes to 0 or 10,000 and the monthly cost to 0 or a
   negative number, accepts a write-off pattern and then pursues it again after items were closed by it. Look for: divisions by
   zero, ROI that turns absurd, items that do not come back when a decision is reversed, history that loses who decided.
8. The clock and calendar: actions at 23:59 and 00:01 UTC and in US time zones, a run that spans midnight, the day daylight saving
   changes, December 31 to January 1, February 29, deadlines on weekends and holidays. Look for: a deadline off by one day, an item
   expired a day early (missed money) or late (wasted work), a follow-up date in the past.

## Rules
- No real faxes, emails, Dropbox writes, TriZetto or AI calls: unset every SRFAX_*, DROPBOX_*, TRIZETTO_*, ANTHROPIC_* and mail
  setting; use the fake fax line and fake clients the tests use (tests/test_fax.py, tests/fakes.py). If a step would reach a real
  service, stop and report it as a finding.
- Real data only on copies, in your own folder under ${S}/chaos/<you>/; screenshots with the names switch off or on invented data;
  everything you return is aggregate or uses RemitWatch finding ids. Delete your copies at the end.
- Drive the real app: the server (uvicorn on a free localhost port) and the serverless mode (REMITWATCH_MODE / the cloud fixtures),
  in Chromium with Playwright, and the API directly. Record each finding with: persona, exact steps (numbered), what a sane system
  does, what RemitWatch did, the evidence (screenshot path, response body with identifiers masked, a failing invented test), the
  harm (money, wrong fax, hidden work, PHI, trust, confusion), severity (high | medium | low), and the file and function at fault
  when you can find it.
- Confusion counts: a point where a newcomer would do the wrong thing, or an expert is slowed by a needless step, is a finding
  (severity by harm).
