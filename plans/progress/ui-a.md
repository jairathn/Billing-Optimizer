# Unit ui-a: progress (plans/briefs/2026-11-28_ui.md, unit ui-a)

Branch `wip/ui-a`, worktree `$S/tk/wt/ui-a` (base: integration tip dc92143). Demo on port 8771, private folder
`$S/tk/units/ui-a` (site, shots, font sources). Invented data only.

## Steps (in the brief's order)

1. P0 stylesheet: DONE (commit "ui-a P0").
   - `remitwatch/static/rw.css` from the design's built stylesheet with CRITIQUE_premium section 7 applied in place
     (the one-off transform is `$S/tk/units/ui-a/p0_css.py`; from now on rw.css is the source, edited by hand).
   - Fonts: Inter 4.1 (official release, OFL) subset with both axes (opsz 14-32, wght) and the features of change 6:
     `fonts/inter-latin-opsz-wght.woff2` 74 KB, `fonts/inter-latin-ext-opsz-wght.woff2` 130 KB, `fonts/OFL-Inter.txt`.
     Command (fontTools in `$S/tk/units/ui-a/venv_fonts`):
     `pyftsubset InterVariable.ttf --flavor=woff2 --unicodes=<the two ranges in rw.css section 0> --layout-features=kern,calt,ccmp,locl,mark,mkmk,tnum,pnum,case,zero,ss02,frac,numr,dnom`
   - Icons: `plans/tools/ui/icons.py --write` writes rw.css's icon block (between the @icons markers) and
     `remitwatch/static/icons.svg` (the sprite pages use). New marks: st-open, st-half, minus, monitor, sidebar, command, corner.
   - Dark tokens: text-3 #949ca9 and accent-soft-2 #292c55 (caption on the selection fill passes 4.5 in both themes).
   - The three page shells link `/static/rw.css?v=2026-11-28` and preload the Latin font; no `<style>` block left.
   - Tests moved (same guarantee, read where the styles live now): test_ema_pages2 (every work type has its style),
     test_ema_pages (a printed page never carries the blind-spot banner). New: tests/test_style.py.
   - Inline `style=` count: 282 at P0 (tests/test_style.py STYLE_ATTRS_MAX; lower it as styles go).
2. P1 shell: next.
3. P4 Today.
4. Brand finish.
5. docs/DESIGN.md.

## Numbers measured

- P0, 1400x900 light (plans/tools/ui/checks.js): today sizes 6, accent 2, filled 1; worklist first row at 865 px (old
  markup), data, /upload (2 filled buttons: its own page), /setup: no overflow, no script error.

## Next command

    cd $S/tk/wt/ui-a && NODE_PATH=$(npm root -g) node plans/tools/ui/checks.js --base http://127.0.0.1:8771 --user <demo user> \
      --pass <demo password> --owner <demo owner password> --shots $S/tk/units/ui-a/shots/now
    (the demo's sign-in is in the docstring of $S/tk/ui/demo/serve_demo.py; never in the repository)

## Open questions

- none yet
