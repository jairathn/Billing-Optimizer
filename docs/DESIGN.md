# RemitWatch design system

How RemitWatch's pages look and why, for the next person who changes them. The rules here are measured by tools and
tests in this repository (section 9), so a change that breaks one fails a check instead of slipping through.

Files: `remitwatch/static/rw.css` (the one stylesheet), `remitwatch/static/fonts/` (Inter), `remitwatch/static/icons.svg`
(the icon sprite), `remitwatch/static/brand/` (the mark and favicons), `remitwatch/static/shell.js` (the shell every page
shares). The app serves all of them from `/static` on both hosts; no page asks another site for a font, a style or a
script.

## 1. Principles

**Every screen opens with the one thing to do next and what it is worth.** On Today it is the next step of the plan
with its dollars and the one filled button. On Work it is the top row, ranked by money. In the item panel it is the next
step sentence and its main button. On Data it is the failing check with its dollars. Nothing above that competes with it.

The rules:

1. **One next action, with its money, first.** Exactly one filled (primary) button per view. Every other button is
   secondary or quiet.
2. **Money ranks the work, the deadline breaks the tie.** The due date before the amount; the amount right-aligned in
   tabular digits.
3. **Color is for exceptions.** The page is ink on calm neutrals. The accent has three jobs only: the one filled button,
   the focus ring, and the selection rule (the 2 px edge on the next step and the open row). Red means money lost or a
   deadline inside 7 days, amber means late or blind, green means paid with proof. A state that is neither (Work now,
   Waiting) is a neutral chip with its own mark, never a color.
4. **Flat at rest.** A hairline border does the work. Nothing that sits still casts a shadow: buttons, panels, cards,
   tiles and lists are flat. Only what floats does: menus, toasts, the palette, dialogs and the docked item panel.
5. **Proof on request, never removed.** The verdict in one line with its proof; the evidence one click away.
6. **Quiet until blind.** Feed health lives in the header's pill, with dollars. A banner appears only when a feed is late
   or the app is in safe mode. The other self-checks live on the Data page, with a dot on Data in the sidebar.
7. **Plain words.** A verdict or a next step is 20 words or fewer. No codes, setting names or file paths on a biller's
   page. No em dashes anywhere.
8. **Same on both hosts.** One stylesheet and one font, served by the app itself.
9. **Readable by everyone.** WCAG AA contrast in both themes, a visible focus ring, every action by keyboard, never
   color alone.

## 2. Type

**Font.** Inter 4.1 (OFL; the licence ships as `fonts/OFL-Inter.txt`), self-hosted as two subsets (Latin, and Latin
Extended) with both variable axes: weight 100 to 900 and optical size 14 to 32. The page sets
`font-optical-sizing: auto`, so titles and figures use the Display cut and body text the text cut. A size-matched local
fallback (`InterFallback`) keeps lines from moving when the font arrives. Each page head preloads the Latin file.

To rebuild the files from the official release (fontTools):

    pyftsubset InterVariable.ttf --flavor=woff2 --unicodes=<the two ranges in rw.css section 0> \
      --layout-features=kern,calt,ccmp,locl,mark,mkmk,tnum,pnum,case,zero,ss02,frac,numr,dnom

**Scale.** At most six sizes on any one screen.

| Token | Size / line | Used for |
|---|---|---|
| `--fs-xs` | 12 / 16 | Labels, table heads, chips, the line under a figure |
| `--fs-sm` | 13 / 18 | Table cells, secondary lines, small buttons |
| `--fs-md` | 14 / 20 | Body text, buttons, inputs |
| `--fs-lg` | 16 / 24 | Section titles, the next step |
| `--fs-xl` | 20 / 28 | The item panel's title, page titles on a phone |
| `--fs-2xl` | 24 / 32 | Page titles |
| `--fs-fig` | 28 / 34 | Figures (Today's Work now, tiles) |
| `--fs-big` | 40 / 44 | The one big figure of a page (Results) |

Three weights: 400, 500 for controls and labels, 600 for titles, figures and emphasis. Titles from 20 px take a slight
negative letter spacing.

**Numbers.** Columns of money use tabular digits so they line up. A hero figure (Today's Work now, Results' money back)
uses proportional digits: tabular digits look uneven at 28 px and up.

**Identifiers** (claim numbers, codes) are set in Inter, not a monospace font, with tabular digits and the
disambiguation set (`"tnum" 1, "ss02" 1`): one typeface per row on every computer, and 0 and O, 1, I and l told apart.

## 3. Words, numbers and dates

- Money: whole dollars in lists, tiles and the plan (`$1,374`); cents in the item panel and anything a person copies
  (`$1,374.27`). A figure that is not known says so, never `$0.00`. Parts shown beside a whole add up to the whole as
  shown (Today's New is the whole's dollars less the follow-ups' dollars).
- Dates: `Dec 15` in the current year, `Dec 15, 2027` otherwise; `in 17 days` under a deadline. Times in the practice's
  time zone. Never ISO dates on a biller's page.
- Counts with their money: `36 items · $3,472`.
- A page title carries counts, never patient data: browser history can sync to personal accounts.

## 4. Space, corners and depth

A 4 px grid: `--sp-1` 4 through `--sp-16` 64. Cards pad 20 (16 on a phone); pages pad 32 on a desk, 24 under 1280 px, 16
on a phone. Corners: 4 chips, 6 small buttons and segments, 8 buttons and inputs, 12 cards and tiles, 16 dialogs, full
for pills and counts. Shadows (`--shadow-md` to `--shadow-xl`) only on what floats (rule 4).

## 5. Color

Every color is a token on `:root`, with a second set for dark mode. The dark set is written twice (under
`prefers-color-scheme: dark` for the system theme, and under `:root[data-theme="dark"]` for a person who chose Dark),
and `tests/test_style.py` fails if the two differ.

| Role | Token | Light | Dark |
|---|---|---|---|
| Canvas | `--bg` | `#f7f8fa` | `#0b0d11` |
| Cards, panel | `--surface` | `#ffffff` | `#13161b` |
| Quiet fill, table head, hover | `--surface-2` | `#f5f6f8` | `#1a1d23` |
| Pressed, the open row, meter track | `--surface-3` | `#eceef2` | `#23272f` |
| Divider | `--border` | `#e6e8ec` | `#262a32` |
| Secondary button edge | `--border-strong` | `#d0d5dd` | `#363c47` |
| Input edge, link underline (3:1) | `--border-control` | `#848c99` | `#6f7886` |
| Text | `--text` | `#101828` | `#eceef1` |
| Secondary text | `--text-2` | `#475467` | `#aab1bc` |
| Captions, table heads | `--text-3` | `#5f6779` | `#949ca9` |
| The filled button | `--accent` | `#4f46e5` | `#5a52ea` |
| Focus ring | `--focus` | `#4f46e5` | `#a5a6ff` |
| Selection fill | `--accent-soft-2` | `#e0e7ff` | `#292c55` |
| Danger text / fill | `--danger` / `--danger-soft` | `#b42318` / `#fef3f2` | `#f07a6e` / `#351c1d` |
| Warning text / fill | `--warning` / `--warning-soft` | `#b54708` / `#fffaeb` | `#e5a93a` / `#31260f` |
| Success text / fill | `--success` / `--success-soft` | `#067647` / `#ecfdf3` | `#4cb782` / `#11291c` |
| Neutral chip text / fill | `--neutral-fg` / `--neutral-soft` | `#344054` / `#f2f4f7` | `#d0d5dd` / `#23272f` |
| The brand tile | `--inverse-bg` / `--inverse-fg` | `#101828` / `#f9fafb` | `#eceef1` / `#101828` |

Links are ink with a `--border-control` underline; the underline takes the accent on hover. Charts take their colors
from `--viz-*` (four series and a context gray, set for both themes), never from a library's default palette.

**Contrast.** Every text pair a page uses passes 4.5:1 and every control edge or focus ring 3:1, in both themes, the
open row and the hover fill included. `tests/test_style.py` lists the pairs and checks them from the stylesheet's own
values; `plans/tools/ui/checks.js` measures the rendered text on every page.

## 6. State, status and kinds of work

| State | Chip | Mark | Means |
|---|---|---|---|
| Work now | `.xs-now`, neutral | arrow in a circle | Yours to do |
| Waiting | `.xs-wait`, neutral | clock | On the payer until the follow-up date |
| Done | `.xs-done`, green | check | Paid, or settled with proof |
| At risk | `.xs-risk`, red | triangle | Closes within 7 days, or money lost |
| Can't be checked | `.xs-warn`, amber | eye struck through | A feed is missing: the app is blind here |
| Accepted | `.xs-accepted`, gray | minus in a circle | A person or a policy let it go |
| Expired | `.xs-expired`, gray | hourglass | The window closed |

A person's status (To do, Started, Sent, Paid, Lost, Let go) is a mark and a word (`.st`), never a filled pill, so
Waiting and Sent are never confused. Kinds of work (`k-fix`, `k-auth`, `k-note` and the rest, listed in `today.js`
KIND_STYLE) are neutral labels told apart by an icon.

## 7. The shell (`static/shell.js`)

The same shell on every page, the app, Upload and Setup:

- **Sidebar** (232 px): the brand; Today; Work with its views (Denials, Bundling, Downcoding, Contracts, Fees and
  takebacks), each with the red count of what closes within 7 days; Faxes; Results. Then Insights (Bigger wins,
  Progress, Analysis), Settings (Data with its dot when a self-check fails, Upload from EMA, Setup), and Owner behind the
  lock (Write-off policy, Providers, Calculator). At its foot the theme (System, Light, Dark) and the button that narrows
  it to a 64 px rail (`[`). Routes stay as they were (`#today`, `#denials`, `#fees` ...).
- **Header**: the search (Find a claim), the health pill, the alerts bell, Help (How RemitWatch works, Keyboard
  shortcuts, Search and go to).
- **The health pill** says in dollars what the app cannot check, worst first: safe mode; a feed behind with the dollars
  unchecked; how far the remittances reach for most payers; payments with no remittance; overdue EMA exports. On the
  Vercel site it always says when the site last analysed and that it analyses only when someone presses Run the
  analysis now. A click lands on the Data section that explains it.
- **Search and go to** (Ctrl K, or Cmd K on a Mac): the places, a place matching what was typed first, then Find a
  claim.
- **Titles**: `(4) Work · Denials · RemitWatch`, the count being what closes within 7 days.
- **Phone** (640 px and narrower): a one-row header with the place's name, the pill and the bell; a tab bar (Today, Work,
  Results, Find, More); More opens the sidebar as a sheet. List rows become cards that keep the box that picks a row.
- **Browser storage** holds only per-person conveniences (the theme, the sidebar's width, the first-visit guide), each
  read and write wrapped so the page works without it. A small script in each page's head sets the theme before the
  first paint, so nothing flashes.

| Width | Layout |
|---|---|
| 1280 px and wider | Sidebar; the item panel docks on the right and the list stays beside it |
| 1024 to 1279 px | Sidebar; the panel opens over the page |
| 641 to 1023 px | The sidebar as a rail; two-column grids stack |
| 640 px and narrower | Tab bar and one-row header; rows as cards; the panel as a full sheet |

Layers: sticky header 10, sidebar 15, panel 30, bulk bar 35, dialog and palette 40, toast 50. Motion is short (120 ms
hover, 200 ms panel) and off for people who ask their system for less motion.

## 8. Components

- **Page head**: an eyebrow (the date on Today), the title, at most one line under it.
- **Buttons**: one filled per view (`.primary`); secondary has a border and no fill; quiet (`.ghost`) has neither. Inset
  highlight only on the filled one.
- **Banners** (`.banner`): `stale` for a late feed or the blind spot, `danger` and safe mode for a stop, `info` neutral.
  Only Today and the pages whose figures it changes show one.
- **Today's strip** (`.td-strip`): the whole (Work now) with a composition bar of its parts (New, Follow-ups due) and
  what in it closes within 7 days and is not sent yet, in red: one definition with the plan's step 1, the red numbers
  on Work's views and the count in the title. Beside it the week (sent or paid) and what deadlines took. Each figure
  opens the list it counts.
- **The plan** (`.plan`, `.step`): fixed columns `28px 1fr 7.5rem 9rem` (number, words, money, button) so money and
  buttons line up. The next step is `is-next`: tinted, the 2 px selection rule, the one filled button `Start (N)`. Other
  steps say `Start`. A done step is one line with a check and a quiet Review link.
- **Rows and lists**: the due date before the amount; the amount right-aligned in tabular digits; the open row on
  `--surface-3` with the selection rule.
- **Sections** (`.secs`): on the long pages (Results, Data, Faxes, Upload, Setup) a page's panels are sections: a
  heading, a hairline and space. A card stays only for something to act on (`.panel.act`) and for figure tiles that
  open a list.
- **The week** (`.rs-figs`, `.rs-fig`): Results opens on this week's four scoreboard figures in a row split by
  hairlines, each opening the list it counts, then "Needs you" and the team. One definition with the board under it.
- **Charts**: Results draws money back by week itself (`.rs-chart`, SVG): the last complete week in `--viz-accent`,
  earlier weeks in `--viz-context`, the current week outlined and labeled "so far", the money back since the first
  flag as a line in `--text-2`. Chart.js charts read `--viz-1` to `--viz-4` and the context grays through index.html's
  `palette`, take their font, ink and grid from the tokens, and are drawn again when the theme changes. Months read
  "Jan 2026", "Feb"; no label is turned on its side. Every chart has a text equivalent (its label and a table).
- **The bell** (`.bell-menu`): the five newest alerts in plain words, newest first, and See all.
- **Icons**: one set of plain 24 px stroke icons drawn for RemitWatch, shown at 16 to 22 px. Pages draw them from the
  sprite (`<svg class="i"><use href="/static/icons.svg#i-search"/></svg>`); the stylesheet draws state marks, kinds of
  work and small marks as masks that take the text color. A pseudo-element draws a mark, never words.
- **The mark**: a remittance slip with a check, one flat color on an inverse tile (`static/brand/icon.svg` follows the
  theme; `icon-32.png` and `icon-180.png` are drawn from it). The wordmark is Inter's Display cut, 600, -0.02em.

## 9. Changing it, and the checks that keep it right

- **rw.css is the source**, edited by hand. Its sections are numbered in its own comments. The icon block between the
  `@icons` markers is written by `python3 plans/tools/ui/icons.py --write`, which also writes `static/icons.svg`; run it
  after adding an icon to its ICONS table (`--check` says whether they match).
- **A new look is a class in rw.css**, never an inline `style=`: `tests/test_style.py` counts the inline styles in
  `remitwatch/static/` and the count can only go down (lower its ceiling when a change removes some).
- **Bump the `?v=` of the stylesheet link** in the three page heads on a release that changes it; the test checks the
  three agree.
- **The page checks**: `plans/tools/ui/checks.js` (Playwright, invented data only) visits every page at 1366x650,
  1536x730, 1400x900 and 390x844, in light and dark, and fails on: anything wider than the screen; a script error or a
  browser dialog; more than 6 font sizes or more than 8 accent elements on the first screen; more than one filled
  button; text under its contrast bar; and on Work, a first row lower than 220 px at 1400x900, fewer than 7 rows at
  1366x650 or 7 cards at 390x844, or (with `--panel`) a row that changes height when the panel opens. It signs in with
  the demo's user and password given on its command line (never written in the repository); screenshots go to the
  folder given with `--shots`, outside the repository.
- **The tests**: `tests/test_style.py` (one stylesheet, the fonts and their licence, served types, every class the pages
  name, every custom property defined, the icon block and sprite, contrast pairs in both themes, inline styles),
  `tests/test_shell.py` (places, the pill, titles, storage, and the shell in a browser), and the page tests that read
  each page.

## 10. What changed from the first design, and why

The first design (kept privately with its critiques) was measured against its own claims, and these were corrected:

- "Five sizes at most" became six: the measured screens used seven to nine; six is the checked limit.
- "Every text pair passes 4.5:1" was false for the caption on the open row and on hover; `--text-3` was darkened (light)
  and lightened (dark), the selection fill adjusted, and the pairs are now tested.
- "Three numbers that add up" showed two parts beside their whole as peers; Today now shows one whole with its parts.
- "One filled button per view" was broken on Work with the panel and on Send to payer; the checks now count them.
- "Cards sit almost flat" had shadows on buttons, cards and tiles; resting things are now flat.
- "Color means state" had the accent on about 15 things per screen; it now has three jobs.
- "Chart pages take the same tokens" was not wired; now every chart takes the `--viz-*` tokens in both themes.
- "Dark mode: cheap" left the banners, charts and the theme control undone; all three are done.
