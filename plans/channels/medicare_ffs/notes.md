# CHANNELS research: medicare_ffs family (read 2026-11-23)

Payer groups: Medicare (pattern items), Medicare KY (CGS J15), Medicare IN (WPS J8), Railroad Medicare, Tricare
(Tricare For Life), Champva - Hac. Public pages only, no log-ins, nothing about the practice or any patient sent anywhere.
Every source is saved in `sources/` (html/pdf plus a `.txt` copy; `_fetchlog.tsv` has the fetch time and HTTP status).
WPS and tricare4u pages render in the browser; their text was read from the payer's own public content API
(`https://www.wpsgha.com/api/content/...`) or from the server-rendered HTML, never from a third party.

## Which MAC applies (835 identities in payers.json)

| 835 payer name | 835 payer id | Contractor | Evidence |
|---|---|---|---|
| KY MEDICARE | 15102 | CGS Administrators, J15 A/B MAC, Kentucky Part B | CGS J15 redetermination form prints "KENTUCKY - (15102)"; CMS EO-0076 lists 15102 KY; 835 phone 866-290-4036 = CGS IVR |
| MEDICARE OF INDIANA / IN MEDICARE | 08102 | WPS Government Health Administrators, J8 A/B MAC, Indiana Part B | CMS Transmittal 2263 (CR 11153, 2019-02-22): "A/B MAC Part B Workload Identifier: 08102 -Indiana (IN)"; 835 phone 866-234-7331 = WPS |
| RAILROAD MEDICARE | 00882, 00320 | Palmetto GBA, Railroad Retirement Board Specialty MAC (national) | same TRN03 and GA state on both ids; HHS-OIG names Palmetto "Railroad Benefit Contractor 882" (search lead only); WPS's own J8B contact page sends Railroad Medicare Part B mail to Palmetto PO Box 10066 Augusta GA |

"Medicare" (pattern items, no 835 identity of its own): route each underlying claim by its 835 payer id with the table
above. Kentucky claims (15102) -> CGS channels; Indiana claims (08102) -> WPS channels; Railroad (00882/00320) -> Palmetto.

## Second level for Part B (settled)

MCPM Pub 100-04 Ch. 29 sec. 320.7 (current chapter Rev. 12423, 12-20-23): Part B North jurisdiction lists Indiana and
Kentucky (and Ohio, Michigan); Part B South "Note: Railroad Retirement Board reconsiderations are also included in this
workload jurisdiction." CMS second-level page (last modified 09/30/2026): Part B North = C2C Innovative Solutions, fax
904-539-4081, portal https://www.c2cinc.com/QIC-Part-B-North; Part B South = C2C, fax 904-539-4090. C2C's own pages give
the same numbers and mail boxes (North PO Box 45208, Jacksonville FL 32232-5208; South PO Box 45300, Jacksonville FL
32232-5300). CGS reconsideration form and WPS J8B guide both name C2C QIC Part B North PO Box 45208.
Always use the QIC named on the redetermination notice; the notice is the tiebreaker.
Part B reconsiderations cannot be filed in myCGS or WPS SNAP (both portals file 2nd level for Part A / HHH only).

## WPS J8 Indiana fax: which number is current (the known conflict)

Indiana appeals / clerical error reopening fax = (608) 224-3504. Michigan = (608) 224-3502.
- WPS "How to Appeal a Claim Determination" (J8B block, content updated 2025-01-07): Indiana (608) 224-3504, Michigan (608) 224-3502.
- WPS Indiana Appeals/Clerical Error Reopening Fax Cover Sheet (form A08, the sheet WPS requires on every fax): "INDIANA FAX ... Fax Number: 608-224-3504".
- WPS J8 Part B Redetermination of an Overpayment Request Form (02/18/2026): Indiana (608) 224-3504.
- WPS How to Request a Clerical Error Reopening (updated 2026-10-20): "use the appropriate fax cover sheet and fax number for your state. Each fax cover sheet lists the fax number for that state."
- Outlier: WPS J8 Part B Redetermination Request Form (02/19/26, file replaced 2026-03-27) prints Indiana (608) 224-3502 and Michigan (608) 224-3504, the two swapped.
Verdict: 3504 for Indiana (four WPS documents, including the state cover sheet and the newest guide); the redetermination
form has the two numbers swapped. Both numbers are WPS appeals lines, so the form's error misroutes inside WPS rather than
to a stranger, but the app should fax Indiana to 3504 with the Indiana cover sheet and say why. Worth one call to WPS
(866-234-7331) to report the form error. "Kentucky vs Indiana" in the task means Indiana vs Michigan on the WPS forms:
CGS publishes no appeal fax at all.

## Reopenings vs redeterminations

- Reopening (clerical error: codes, modifiers, units, dates, data entry): not an appeal; within 1 year of the initial
  determination (4 years with good cause) - MCPM Ch. 34 sec. 10.6.2. CGS: myCGS is "the best vehicle", else mail the
  hardcopy form; telephone reopenings ended 08/01/2026; no fax. WPS: portal (once per claim), phone 866-234-7331, fax with
  state cover sheet, or mail. Palmetto RR: fax (803) 462-2218 or mail (AP-RRB-B-1020; AP-RRB-B-1021 when over 1 year).
- Redetermination (1st level appeal): 120 days from receipt of the initial determination (receipt presumed 5 days after
  the notice date). Medical necessity, MUE, documentation disputes. CGS: myCGS preferred, else mail; "Redeterminations do
  not accept faxed request." WPS: portal, fax, mail. Palmetto RR: eServices, fax, mail.
- Overpayment appeals: same redetermination; to keep recoupment halted file within 30 days of the demand letter (WPS
  guide 239, Section 935).
- Unprocessable (MA130) claims have no appeal or reopening: send a new claim (electronic unless ASCA small-provider).

## No-fax findings (with the source that shows the accepted channels)

- CGS J15 redeterminations and reopenings: job aid rev. 12/16/2025 "Redeterminations do not accept faxed request";
  myCGS or mail only. CGS phone/fax page (10.27.2026) lists faxes only for congressional offices, general correspondence
  (615.782.4490) and overpayment recovery (KY 615.664.5916).
- Tricare For Life appeals: tricare4u FAQ "MUST be mailed ... WPS TRICARE For Life ATTN: APPEALS P.O. BOX 7490" or the
  provider portal; tricare.mil appeals page lists the same box, no fax.
- CHAMPVA decision reviews: VA page (06/03/2026) lists mail only, VFMP Appeals PO Box 600 Spring City PA 19475.

## Single-source numbers (shown, but a person must confirm against the payer's letter before the first send)

- CGS KY medical review ADR fax 615-664-5964: CGS article "ADRs: How and What to Send" (updated 01.17.2025) only.
- WPS J8B medical review ADR fax (608) 224-3512: WPS ADR guide (updated 2024-12-26) only.
- DHA Appeals, Hearings and Claims Collection Division fax 303-676-3616 (TRICARE formal review): tricare.mil appeals page only.
- Board of Veterans' Appeals fax 844-678-8979: va.gov Board Appeals page (10/22/2026) only; VA Form 10182 (JUN 2026) lists mail only.
- Palmetto RR ADR fax: no current Palmetto publication reachable (new site renders in the browser; legacy pages gone);
  not recorded. Use the fax or address printed on the ADR letter, or eServices.

## Other conflicts and notes

- CGS redetermination address: form "CGS, Attn: J15 Part B Appeals Department, PO Box 20018, Nashville, TN 37202";
  addresses page "J15 - Part A/B Correspondence ... PO Box 20018"; same box. Courier: 26 Century Blvd Suite NT600 Nashville TN 37214.
- CGS ADR mail: article says "CGS Administrators, LLC, Medicare Part B, 26 Century Blvd, Suite NT600"; addresses page says
  "J15 - Part A/B Correspondence ... 26 Century Blvd Suite NT600" for Medical Review ADRs. Same street address.
- CGS PWK cover sheet (2018) fax KY 1.615.664.5952 is for unsolicited PWK documentation with an electronic claim; it says it
  must not be used for ADR responses. WPS PWK cover (2019) Indiana (608) 224-3505, same limit. Not appeal or ADR lines.
- TFL handbook (Updated August 2026) says claims in the U.S. "must be filed within three years"; tricare4u FAQ and the
  TFL page on tricare.mil say one year. Not a channel question; flagged for the deadline owner.
- TFL: "Allowable charges" are not appealable; network providers are not proper appealing parties (TFL handbook Fig. 7.1);
  Medicare-covered services are appealed to Medicare first (tricare.mil table).
- CHAMPVA paper claims now go to "VCC - Payment Operations, CHAMPVA Claims, PO Box 30750 Tampa, FL 33630-3750" (VA
  CHAMPVA page 10/22/2026); older Denver and Spring City claim boxes are out of date.
- Palmetto RR eServices, myCGS and WPS SNAP all need a log-in; the app can only link to the login page and hand over the packet.

## How the facts were checked

- `scripts/build.py` writes `research.json` and refuses to write it unless every quoted passage (passages joined by
  " ... ") is found, after whitespace and ligature normalisation, in the saved copy of its source under `sources/`.
- tricare.mil answers scripted downloads with a bot challenge, so its two pages (Appeals, last updated 10/21/2026; Claims
  Filing Addresses, last updated 9/23/2026) were read with WebFetch; those quotes are as returned and have no local copy.
  The TFL Handbook PDF (tricare.mil, Updated August 2026) is saved.
- Fax numbers with two independent official publications: WPS Indiana (608) 224-3504 (four WPS documents); QIC Part B
  North 904-539-4081 and South 904-539-4090 (CMS page and C2C pages); Palmetto RR (803) 462-2218 (redetermination and two
  reopening forms); CGS overpayment recovery KY 615-664-5916 (phone page, offset form, OPR form); WPS overpayment
  notification (608) 223-7550 (form and Contact Us); TFL correspondence 608-301-2114 (Contact Us and FAQ).
- One publication only (person confirms against the payer's letter before the first send): CGS ADR KY 615-664-5964,
  WPS ADR (608) 224-3512, DHA formal review 303-676-3616, Board of Veterans' Appeals 844-678-8979.
