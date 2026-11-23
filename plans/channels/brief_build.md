# CHANNELS build brief (hotfix 3 on the live code, then carried into push 2)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad

## The owner's words (2026-11-23)
"Will you add the fax numbers for whichever you know for a fact are correct, and take fax? ... just have the billers/practice
manager hit 'confirm' rather than making them enter it. Ideally you find fax numbers for 100% of them, or just make it super easy
to link to the submission portal for claims. I don't really want people needing to leave the app for much. It should be a super
delightful experience. A 100x improvement over the standard today."

## Input
${S}/channels/channels.json (one entry per payer group, and per insurer inside groups that hold many; routes per purpose with
channel, value, label, form, status verified | unverified | conflict, and sources with url, title, doc_date, quote) and
${S}/channels/CHANNELS_REPORT.md. Only status verified is ever offered as a number to confirm. conflict and unverified are shown as
"where to look", never as a number to send to.

## Build (worktree from main after hotfix 2 is on main; module + small hooks so push 2 can take it as files)
1. remitwatch/channels.py and remitwatch/data/payer_channels.json (the directory the app ships: payer facts only, no practice data;
   tests/test_docs.py's public-repo scans stay green). Matching to the store's payer groups by the group name and, for insurers
   inside a many-insurer group, by the 835 payer name and id; anything ambiguous matches nothing.
2. Suggested numbers. GET (signed-in, both hosts, from the copy served) the payers with their suggested appeal and second-level
   numbers, each with its source title, date, the quote and the link, next to what is saved now. A saved number that differs from
   the directory is shown as a difference for a person to settle; never changed automatically.
3. One-click confirm. POST (signed-in; any biller or the practice manager, no owner password) confirms the directory's number for a
   payer and purpose: refused unless the number sent equals the directory's verified number now; recorded through the existing
   payer_fax journal kind with who and when and "confirmed from <source title>, <doc date>". Typing any other number keeps today's
   rule (the owner's password where one is set). A confirmed directory number is used by sends, retries and follow-ups like any
   confirmed number.
4. The item panel says where this appeal goes and does it:
   - fax route: "Fax the appeal to <payer> at <number>" (when the number is not confirmed yet: the source quote, the link, and
     [Confirm and fax], which confirms for the payer and sends this item in one click);
   - portal route: "<Payer> takes this on <portal>" with [Open <payer>'s page] (the payer's own page for that request, new tab),
     [Download the packet] (the existing packet), [Copy claim number]; then [I submitted it] with an optional reference number,
     which marks the item Sent with the channel and reference in its history and starts the follow-ups as a fax does;
   - mail route: [Print for mail] (the packet with an address page placed for a window envelope) and [Mailed], which marks it Sent
     with the channel in its history and starts the follow-ups;
   - the payer's required form, when the directory names one: a link to it, and a line saying it must go with the packet.
5. The Faxes page: a "Payer numbers" list, largest open dollars first, each payer with its route, the suggested number with its
   evidence and [Confirm], the saved number and who confirmed it, and payers with no verified route marked "find the number" with
   where to look.
6. Hotfix 2's guarantees hold for every new button (bound to its item, disabled while in flight, seen_status on status changes,
   strict JSON true for any override). No new dollar floor. Phone width works. Plain English, no em dashes.
7. Tests: invented directory entries and stores, both hosts; confirm refuses a number that is not the directory's; a confirmed
   number is used by a send; a person's saved number is never replaced; portal and mail Sent record channel and reference and start
   follow-ups; shared groups match per insurer or not at all; Playwright for the panel (fax, portal, mail) at 1400 and 390 px.
8. Docs: OPERATIONS.md (how numbers are suggested and confirmed; how to settle a difference), the directory's refresh (how to re-run
   the research and verification), and the release note line.

## ADDED (guard audit L1-19): a typed number is not trusted by automatic sends until it is checked
Today a person's FIRST save of a payer's fax number (a typo included) is used by every send nobody presses a button for
(retries, follow-ups, bulk). With the directory: a typed number that equals the directory's verified number counts as
confirmed; a typed number that differs from it, or for a payer the directory has no verified number for, is used only by sends a
person presses until a second person (or the owner) confirms it on the Faxes page with the number in front of them. Tests for both.

## ADDED 2026-11-23 03:50 (the owner: "is there a way to automatically confirm that the payer received the fax? And it didn't just
## go into the abyss? Or a backup if a fax isn't answered 3 times or something (payer will say they never received)?")
P1. Proof of delivery for every fax. From the fax line's own status record (SRFax): when it went, to which number, pages the payer's
    machine accepted against pages in the packet, the call's length, the receiving machine's own identifier when the line gives
    it, and the line's fax id. A send whose accepted pages are fewer than the packet's is a failure: the whole packet is sent again
    by the existing retry rules. Keep SRFax's copy of the sent fax where the line offers it. Make a one-page "Fax delivery record"
    (PDF) per sent fax, attached to the item and shown in its panel ("Received by the payer's fax line on <date> at <time>, all <n>
    pages"). Learn each payer number's receiving identifier; a change is shown on the Faxes page (a possible wrong number).
P2. The answer clock. Each appeal's follow-up date comes from the payer's own decision deadline for that kind and level (the
    verified deadlines from the leverage research, ${S}/lever/, once its synthesis exists; until then today's follow-up timing),
    counted from the delivery record's date. The second request encloses the delivery record and the original packet again, and
    says when the decision was due and under which rule, so "we never received it" has its answer on the page.
P3. After three failed sends to a number, or two requests with no answer, the item switches route by itself to the next one the
    directory lists (portal or mail) and says so in its history; the number is flagged on the Faxes page for a person to check.
Tests: invented SRFax answers (full, partial, failed, busy); the record PDF holds no patient detail beyond what the packet itself
already sent to that payer; both hosts.
