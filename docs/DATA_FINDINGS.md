# What the remittances say: where Owensboro Dermatology is losing money

Generated 2026-11-06 from 31,547 unique 835 remittances (188,089 claims, 408,876 service lines), payment dates 2024-01-01 to 2026-09-10, $29,371,752 paid. Detection version 1.1. Dollar figures are the allowed-amount shortfall against the payer's own usual rate (or Medicare where noted), not charges. All findings are open until a biller marks them otherwise.

## The one-paragraph answer

Across the whole archive the detectable, still-unrecovered leakage is about $2,448,472. Classic E/M downcoding, the thing the practice asked about, is $61,327 of it. The money is in bundling ($173,657), denials that were never worked ($626,242), contract rates below Medicare ($877,022 at Medicare parity), underpayment against the payer's own rate ($182,702), and fees plus takebacks ($527,522). A further $802,873 across 5,456 lines was initially denied or bundled and then paid on a later remittance, which is the rework the billers did do; those are marked resolved and excluded from the open figures.

## By year and category

| year | downcoding | bundling | denial | underpayment | fees | contract | total |
|---|---:|---:|---:|---:|---:|---:|---:|
Open findings only (resolved and dismissed excluded).

| 2023 | $816 | $3,721 | $89,222 | $1,994 | $0 | $5,528 | $101,281 |
| 2024 | $2,926 | $58,155 | $234,339 | $55,021 | $72,267 | $394,390 | $817,097 |
| 2025 | $4,095 | $36,766 | $205,663 | $81,243 | $197,789 | $281,640 | $807,196 |
| 2026 | $53,490 | $75,015 | $94,656 | $44,424 | $257,466 | $195,465 | $720,516 |

## By signal

| signal | category | what to do | open findings | open dollars | paid later (rework) |
|---|---|---|---:|---:|---:|
| CONTRACT_RATE_GAP | contract | renegotiate | 51 | $615,666 | $0 |
| TAKEBACK | fees | review | 139 | $460,343 | $0 |
| MA_BELOW_MEDICARE | contract | review | 132 | $261,356 | $0 |
| UNDERPAID_VS_OWN_RATE | underpayment | appeal | 2,989 | $180,541 | $0 |
| DENIED_NONCOVERED | denial | review | 1,030 | $105,959 | $94,604 |
| DENIED_AUTH | denial | appeal | 753 | $94,823 | $20,668 |
| DENIED_MEDICAL_NECESSITY | denial | appeal | 682 | $86,773 | $178,440 |
| EM_BUNDLED_MOD57_MAJOR | bundling | appeal | 730 | $70,990 | $41,620 |
| DENIED_DUPLICATE_UNPAID | denial | review | 370 | $70,608 | $0 |
| FEE_SKIM | fees | opt_out | 1,122 | $67,179 | $0 |
| DENIED_MISSING_INFO | denial | correct_and_resubmit | 462 | $59,051 | $107,525 |
| PROCEDURE_BUNDLED | bundling | appeal | 239 | $52,305 | $150,083 |
| CODE_CHANGED | downcoding | review | 162 | $52,166 | $0 |
| CLAIM_DENIED_UNPAID | denial | review | 327 | $41,490 | $0 |
| DENIED_FREQUENCY | denial | appeal | 318 | $41,177 | $46,465 |
| EM_BUNDLED_MOD25 | bundling | appeal | 578 | $37,538 | $38,826 |
| CLAIM_DENIED_UNPAID | denial | appeal | 824 | $34,104 | $0 |
| CLAIM_DENIED_UNPAID | denial | correct_and_resubmit | 143 | $22,660 | $0 |
| DENIED_MISSING_MODIFIER | denial | correct_and_resubmit | 205 | $20,189 | $14,321 |
| DENIED_TIMELY_FILING | denial | review | 142 | $19,450 | $1,041 |
| DENIED_PROVIDER | denial | review | 72 | $9,856 | $17,342 |
| DENIED_OTHER | denial | review | 49 | $9,357 | $20,131 |
| DENIED_CODING | denial | correct_and_resubmit | 66 | $7,559 | $14,207 |
| DOWNCODE_SVC06 | downcoding | appeal | 149 | $4,080 | $0 |
| DOWNCODE_LEVEL_DENIED | downcoding | appeal | 71 | $3,548 | $0 |
| EM_BUNDLED_MOD57_MINOR | bundling | correct_and_resubmit | 36 | $3,457 | $756 |
| ADDON_BUNDLED | bundling | appeal | 21 | $3,254 | $20,055 |
| DENIED_MISSING_PRIMARY | denial | review | 40 | $3,186 | $27,193 |
| EM_BUNDLED_MOD24 | bundling | appeal | 47 | $3,176 | $1,710 |
| EM_BUNDLED_NO_MODIFIER | bundling | correct_and_resubmit | 40 | $2,937 | $7,887 |
| MPPR_EXCESS | underpayment | appeal | 130 | $2,161 | $0 |
| DOWNCODE_RATE | downcoding | appeal | 46 | $1,509 | $0 |
| DOWNCODE_CHARGE | downcoding | appeal | 1 | $24 | $0 |

## By payer

| payer | kind | findings | dollars | of which appealable now |
|---|---|---:|---:|---:|
| Anthem / BCBS | commercial | 2,543 | $817,618 | $19,208 |
| Medicare KY (CGS J15) | medicare | 1,267 | $478,504 | $7,637 |
| Humana Medicare Advantage | medicare_advantage | 1,768 | $389,539 | $0 |
| UnitedHealthcare Medicare Advantage | medicare_advantage | 1,212 | $221,094 | $22,853 |
| Medicare IN (WPS J8) | medicare | 853 | $187,442 | $6,236 |
| Anthem / BCBS Medicare Advantage | medicare_advantage | 676 | $94,138 | $0 |
| UnitedHealthcare | commercial | 434 | $46,188 | $11,364 |
| Medicare Supplement carriers | supplement | 551 | $35,142 | $38 |
| UMWA Health & Retirement Funds | commercial | 634 | $30,528 | $3,232 |
| UHC Integrated Services (Wausau) | commercial | 380 | $25,969 | $3,061 |
| UMR | commercial | 338 | $21,411 | $4,613 |
| Aetna | commercial | 291 | $19,974 | $2,785 |
| Self-funded plan TPAs | commercial | 415 | $18,130 | $562 |
| Humana Medicaid | medicaid | 109 | $11,878 | $0 |
| Medicaid KY | medicaid | 39 | $8,769 | $0 |
| Humana | commercial | 40 | $8,172 | $0 |
| Aetna Senior Supplemental (American Continental) | supplement | 34 | $6,366 | $0 |
| Illumifin | other | 39 | $4,544 | $0 |
| Southeastern Indiana Health Organization (Siho) | other | 46 | $2,605 | $0 |
| Medicaid IN | medicaid | 53 | $2,372 | $1,653 |

## Contract rates versus Medicare

Usual allowed amount per code (most common allowed amount on cleanly paid primary lines) as a percent of the same code on this practice's own Medicare remits, same year and provider type.

| payer | year | code | provider type | lines | payer rate | Medicare | % of Medicare | gap at parity |
|---|---|---|---|---:|---:|---:|---:|---:|
| Anthem / BCBS | 2024 | 99214 | nonphysician | 1912 | $64 | $102 | 62% | $73,555 |
| Anthem / BCBS | 2025 | 99214 | nonphysician | 1809 | $64 | $100 | 64% | $65,251 |
| Anthem / BCBS | 2026 | 99214 | nonphysician | 1317 | $64 | $109 | 59% | $58,923 |
| Anthem / BCBS | 2025 | 99213 | nonphysician | 2488 | $51 | $71 | 72% | $49,287 |
| Anthem / BCBS | 2024 | 99213 | nonphysician | 2235 | $51 | $72 | 70% | $47,762 |
| Anthem / BCBS | 2026 | 99213 | nonphysician | 1424 | $51 | $76 | 67% | $35,728 |
| Anthem / BCBS | 2024 | 99214 | physician | 538 | $75 | $120 | 62% | $24,350 |
| Anthem / BCBS | 2024 | 99213 | physician | 943 | $60 | $85 | 70% | $23,707 |
| Anthem / BCBS | 2025 | 99213 | physician | 784 | $60 | $83 | 72% | $18,267 |
| Anthem / BCBS | 2024 | 17110 | nonphysician | 551 | $55 | $88 | 62% | $18,117 |
| UnitedHealthcare Medicare Advantage | 2024 | 77280 | physician | 246 | $167 | $239 | 70% | $17,623 |
| Anthem / BCBS | 2025 | 99214 | physician | 384 | $75 | $118 | 64% | $16,297 |
| Anthem / BCBS | 2025 | 17110 | nonphysician | 524 | $55 | $84 | 65% | $15,547 |
| Anthem / BCBS | 2024 | 99204 | nonphysician | 449 | $104 | $134 | 77% | $13,632 |
| Anthem / BCBS | 2026 | 99204 | nonphysician | 336 | $104 | $142 | 73% | $13,067 |
| UnitedHealthcare Medicare Advantage | 2024 | 77600 | physician | 90 | $338 | $483 | 70% | $13,036 |
| Humana Medicare Advantage | 2024 | 77280 | physician | 271 | $191 | $239 | 80% | $12,943 |
| Anthem / BCBS | 2025 | 99204 | nonphysician | 464 | $104 | $131 | 79% | $12,667 |
| Anthem / BCBS | 2026 | 17110 | nonphysician | 361 | $55 | $86 | 64% | $11,372 |
| Anthem / BCBS | 2026 | 99214 | physician | 190 | $75 | $128 | 59% | $10,002 |
| Anthem / BCBS | 2026 | 99213 | physician | 336 | $60 | $90 | 67% | $9,919 |
| Humana Medicare Advantage | 2025 | 77600 | physician | 384 | $445 | $468 | 95% | $8,989 |
| UnitedHealthcare Medicare Advantage | 2024 | 88305 | physician | 287 | $40 | $66 | 60% | $7,560 |
| Anthem / BCBS | 2024 | 11102 | nonphysician | 477 | $62 | $77 | 80% | $7,212 |
| Anthem / BCBS | 2024 | 77280 | physician | 134 | $186 | $239 | 78% | $7,137 |
| Anthem / BCBS | 2025 | 11102 | nonphysician | 622 | $62 | $74 | 84% | $7,134 |
| UnitedHealthcare Medicare Advantage | 2025 | 88305 | physician | 271 | $38 | $64 | 60% | $6,938 |
| Anthem / BCBS | 2024 | 99204 | physician | 192 | $122 | $157 | 77% | $6,856 |
| Humana Medicare Advantage | 2024 | 99213 | nonphysician | 863 | $65 | $72 | 89% | $6,611 |
| Humana Medicare Advantage | 2025 | 77280 | physician | 572 | $219 | $231 | 95% | $6,607 |

## Medicare program adjustments

- 2023: net MIPS adjustment +$4,344 across 2994 lines
- 2023: sequestration withheld $7,721
- 2024: net MIPS adjustment +$307,984 across 35964 lines
- 2024: sequestration withheld $157,514
- 2025: net MIPS adjustment +$82,364 across 37106 lines
- 2025: sequestration withheld $207,405
- 2026: net MIPS adjustment +$13,568 across 18955 lines
- 2026: sequestration withheld $76,734

## Fees withheld and takebacks

- Fees: UMWA HEALTH & RETIREMENT FUNDS: $14,166 across 145 remittances
- Fees: MUTUAL OF OMAHA COMMERCIAL: $9,979 across 3952 remittances
- Fees: WAUSAU: $5,269 across 595 remittances
- Fees: AETNA - AMERICAN CONTINENTAL INS.CO.: $4,845 across 672 remittances
- Fees: GREAT AMERICAN LIFE: $3,084 across 643 remittances
- Fees: AMERICAN ENTERPRISE GROUP: $2,997 across 1364 remittances
- Fees: UNITEDHEALTH INTEGRATED SERVICE - FORMERLY WAUSAU: $2,887 across 370 remittances
- Fees: MEDICO INSURANCE COMPANY: $2,765 across 1585 remittances
- Fees: UNITED MEDICAL RESOURCES: $2,676 across 278 remittances
- Fees: UMR: $2,363 across 302 remittances
- Fees: ACTUARIAL MANAGEMENTS RESOURCES: $1,636 across 330 remittances
- Fees: WELLABE SERVICES COMPANY: $1,555 across 423 remittances
- Takebacks: KY MEDICARE 2026: $171,610 across 52 recoupments of $500 or more
- Takebacks: KY MEDICARE 2025: $127,390 across 10 recoupments of $500 or more
- Takebacks: MEDICARE OF INDIANA 2026: $63,374 across 17 recoupments of $500 or more
- Takebacks: KY MEDICARE 2024: $46,607 across 11 recoupments of $500 or more
- Takebacks: MEDICARE OF INDIANA 2025: $31,582 across 24 recoupments of $500 or more
- Takebacks: HUMANA 2025: $4,444 across 5 recoupments of $500 or more
- Takebacks: IN MEDICARE 2024: $2,416 across 4 recoupments of $500 or more
- Takebacks: UNITED HEALTHCARE OF ALL STATES 2024: $2,308 across 3 recoupments of $500 or more
- Takebacks: UNITED HEALTHCARE OF ALL STATES 2025: $2,285 across 2 recoupments of $500 or more
- Takebacks: HUMANA 2026: $2,165 across 3 recoupments of $500 or more

## Correctable billing errors (no appeal needed, just a corrected claim)

- DENIED_MISSING_INFO: 462 lines, $59,051
- CLAIM_DENIED_UNPAID: 143 lines, $22,660
- DENIED_MISSING_MODIFIER: 205 lines, $20,189
- DENIED_CODING: 66 lines, $7,559
- EM_BUNDLED_MOD57_MINOR: 36 lines, $3,457
- EM_BUNDLED_NO_MODIFIER: 40 lines, $2,937

## Appeal deadlines (estimated from the remittance date and payer type)

- due within 30 days: 284 findings, $20,520
- due within 90 days: 388 findings, $30,830
- expired: 7,462 findings, $646,942
- later: 396 findings, $33,564

## Caveats

- Expected amounts come from the payer's own remittance history (mode of allowed amounts), not from executed contracts. Load the contracts to replace them.
- Provider type (physician vs PA/NP) is inferred from Medicare allowed amounts; a roster would make it exact.
- Appeal windows are defaults by payer type; confirm against each contract and provider manual.
- Whole-claim denials count as unpaid only when no later remit paid the same claim number.
