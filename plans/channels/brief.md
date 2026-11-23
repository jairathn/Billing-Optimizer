# CHANNELS research brief: where each payer takes appeals, and how RemitWatch sends them there

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad

## The owner's words (2026-11-23)
"Will you add the fax numbers for whichever you know for a fact are correct, and take fax? ... just have the billers/practice
manager hit 'confirm' rather than making them enter it. Ideally you find fax numbers for 100% of them, or just make it super easy
to link to the submission portal for claims. I don't really want people needing to leave the app for much. It should be a super
delightful experience. A 100x improvement over the standard today."

## What this research produces
A directory, per payer the practice bills, of every official channel for provider claim appeals and disputes, each fact with
its proof, so the app can offer: "Fax the appeal to <payer> at <number> (from <payer's document>, <date>) [Confirm]" or "This payer
takes this only on <portal>: [Open the payer's page] [Download the packet] [Copy the claim number] [I submitted it]" or "Mail to
<address> [Print with address page]". A number that is wrong sends a patient's records to a stranger: a fax number goes in only
when its digits are found in the payer's own current official publication, for provider appeals of that product and state, and an
independent second check finds the same.

## The payers
${S}/channels/payers.json: the practice's 87 payer groups as RemitWatch names them, with payer_kind (commercial, medicare,
medicare_advantage, medicaid, supplement, tricare, other), open items and dollars (to order the work), and the 835 identities
seen for each (payer name, payer id, payer state). The practice is in Kentucky and Indiana (Medicare KY is CGS J15 Part B; Medicare
IN is WPS J8 Part B). Two groups hold many insurers ("Medicare Supplement carriers", "Self-funded plan TPAs"): research the
insurers named in their identities one by one.
Earlier leverage research saved notes with some numbers and known conflicts (hints only, never proof): ${S}/lever/*/notes*.md and
${S}/lever/*/research.json (for example WPS J8 lists Indiana redetermination fax 608-224-3502 on one form and 608-224-3504 on
another; UMR's 2026 guide lists post-service appeals by phone or mail only).

## For each payer (and each product or state that differs), find
For each request type: (1) first-level appeal / reconsideration / payment dispute (post-service, provider, claim payment); (2) the
second level; (3) a corrected claim when not sent electronically; (4) medical records the payer asked for:
- channels the payer accepts: fax (exact number, any required cover sheet or form), portal (its name, the URL of the payer's page
  that starts that request, whether the portal is required or preferred), mail (exact address), email if offered;
- the required form, if any (exact name, URL), and what must be attached;
- the filing window of each level, as the source states it;
- for every fact: the official source (URL on the payer's own domain, a CMS or MAC page, the state Medicaid agency, or the payer's
  official PDF), the document's title and date or version, the exact quoted text that holds the number or address, the date you
  read it, and whether the source is public or behind a login;
- conflicts: two numbers for the same purpose, an older document with another number, member appeals vs provider appeals,
  pre-service (utilization) vs post-service (claims), commercial vs Medicare Advantage vs Medicaid. Say which applies and why.
Do not guess and do not use a third-party directory, forum, aggregator or AI summary as proof (they may point you to the source).
If the payer publishes no fax for provider claim appeals, say so with the source that shows the accepted channels.

## Rules
- Read only public web pages and documents. Never log in anywhere. Never send the practice's name, NPI, tax ID, address, or any
  patient, claim or check data to any outside service, search box or URL. Search with payer names, product names and states only.
- Save what you download under ${S}/channels/<family>/ (sources/ for documents, notes.md, and your JSON result).
- Everything you return is about payers (public facts), never about patients.
