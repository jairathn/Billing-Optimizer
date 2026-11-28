"""The design system's contract (docs/DESIGN.md): one stylesheet, one font and one icon sprite, served from /static by the
app itself on both hosts; the classes the pages and the tests name; colors that pass their contrast bars in both themes;
and the inline styles the pages still carry, which may only go down in number."""
from __future__ import annotations

import importlib.util
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "remitwatch", "static")
SHELLS = ("index.html", "upload.html", "setup.html")
FONTS = ("inter-latin-opsz-wght.woff2", "inter-latin-ext-opsz-wght.woff2")
# the inline style= attributes in remitwatch/static (pages and scripts). It may only go down: lower it when a change
# removes some, never raise it (a new look goes in rw.css as a class).
STYLE_ATTRS_MAX = 183
# browser boxes (alert, confirm, prompt) in the pages: none (CRITIQUE_biller change 4, review d-3). Only ever lowered.
BROWSER_BOXES_MAX = 0


def read(name: str) -> str:
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def css() -> str:
    return read("rw.css")


def test_every_page_shell_links_the_one_stylesheet_and_holds_no_style_block():
    versions = set()
    for name in SHELLS:
        page = read(name)
        assert "<style" not in page, name
        m = re.search(r'<link rel="stylesheet" href="/static/rw\.css\?v=([\w.-]+)">', page)
        assert m, name
        versions.add(m.group(1))
        # the Latin font is asked for at once, before the stylesheet finds it
        assert '<link rel="preload" href="/static/fonts/inter-latin-opsz-wght.woff2" as="font" type="font/woff2" crossorigin>' in page, name
    assert len(versions) == 1, versions                      # one version, bumped together on a change


def test_the_fonts_and_their_licence_ship_with_the_app(css):
    for name in FONTS:
        with open(os.path.join(STATIC, "fonts", name), "rb") as fh:
            assert fh.read(4) == b"wOF2", name
        assert f'url("/static/fonts/{name}") format("woff2")' in css, name
    with open(os.path.join(STATIC, "fonts", "OFL-Inter.txt"), encoding="utf-8") as fh:
        assert "SIL Open Font License" in fh.read()
    assert "font-optical-sizing: auto" in css            # the Display cut for titles and figures


def test_the_app_serves_the_stylesheet_fonts_and_icons_with_their_types(monkeypatch, tmp_path):
    """Both hosts serve /static through the app (on Vercel every path reaches the one function): the types must be right
    for a browser to use them, and the files must need no sign-in (they hold no data)."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("REMITWATCH_DB", str(tmp_path / "s.sqlite"))
    from remitwatch import api
    c = TestClient(api.app)
    for path, kind in (("/static/rw.css", "text/css"), ("/static/icons.svg", "image/svg+xml"),
                       *((f"/static/fonts/{f}", "font/woff2") for f in FONTS)):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].split(";")[0] == kind, (path, r.headers["content-type"])


def test_the_stylesheet_has_every_class_the_pages_and_the_tests_name(css):
    def has(cls):
        return re.search(rf"\.{re.escape(cls)}(?![\w-])", css) is not None
    states = ["xs", "xs-now", "xs-wait", "xs-done", "xs-risk", "xs-warn", "xs-accepted", "xs-expired", "xs-quiet"]
    statuses = ["st", "st-in_progress", "st-appealed", "st-resolved", "st-lost", "st-dismissed"]
    kinds = re.search(r"const KIND_STYLE = \{([^}]*)\}", read("today.js")).group(1)
    kinds = re.findall(r"'(k-[a-z]+)'", kinds)
    assert len(kinds) >= 9
    missing = [c for c in states + statuses + kinds if not has(c)]
    assert not missing, missing
    # the printed page never carries the blind-spot banner, nor the shell
    assert re.search(r"@media print\s*\{[^{]*#blindBar", css)


def test_the_stylesheet_says_nothing_in_words_and_no_em_dash(css):
    assert "\u2014" not in css
    # a pseudo-element draws a mark, never words (words belong in the page, where they can be translated and read aloud)
    assert not re.findall(r'content:\s*"[^"]*[A-Za-z][^"]*"', css)


def test_every_custom_property_the_stylesheet_uses_is_defined(css):
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    # set by the pages at run time, each used with a fallback or set where it is used
    runtime = {"--icon", "--pv1", "--tabs-on", "--th-top", "--sticky-top"}
    used = set(re.findall(r"var\((--[\w-]+)\)", css))         # a var() with no fallback
    assert not (used - defined - runtime), sorted(used - defined - runtime)


def test_the_icon_block_and_the_sprite_are_what_the_icon_tool_writes():
    spec = importlib.util.spec_from_file_location("ui_icons", os.path.join(ROOT, "plans", "tools", "ui", "icons.py"))
    icons = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(icons)
    css_text, svg_text = icons.written()
    assert read("rw.css") == css_text, "run: python3 plans/tools/ui/icons.py --write"
    assert read("icons.svg") == svg_text, "run: python3 plans/tools/ui/icons.py --write"


def _tokens(css: str) -> dict:
    light = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\b", css.split("@media (prefers-color-scheme: dark)")[0]))
    m = re.search(r':root\[data-theme="dark"\] \{(.*?)\n\}', css, re.S)
    mq = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)\n  \}', css, re.S)
    pick = lambda block: dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\b", block))
    assert pick(m.group(1)) == pick(mq.group(1)), "the two dark blocks must say the same"
    return {"light": light, "dark": dict(light, **pick(m.group(1)))}


def _ratio(a: str, b: str) -> float:
    def lum(h):
        def ch(c):
            c /= 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b_ = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b_)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# (text, background, bar): every fill a caption or a link can sit on, the open row and hover included (CRITIQUE 7a)
PAIRS = [("text", "surface", 4.5), ("text", "bg", 4.5), ("text-2", "surface", 4.5), ("text-2", "surface-2", 4.5),
         ("text-2", "surface-3", 4.5), ("text-3", "surface", 4.5), ("text-3", "bg", 4.5), ("text-3", "surface-2", 4.5),
         ("text-3", "surface-3", 4.5), ("text-3", "nav-hover", 4.5), ("text-3", "accent-soft", 4.5),
         ("text-3", "accent-soft-2", 4.5), ("text", "surface-3", 4.5), ("on-accent", "accent", 4.5),
         ("on-accent", "accent-hover", 4.5), ("neutral-fg", "neutral-soft", 4.5), ("success", "success-soft", 4.5),
         ("danger", "danger-soft", 4.5), ("warning", "warning-soft", 4.5), ("danger", "surface", 4.5),
         ("warning", "surface", 4.5), ("success", "surface", 4.5), ("text", "warning-soft", 4.5), ("text", "danger-soft", 4.5),
         ("inverse-fg", "inverse-bg", 4.5), ("inverse-2", "inverse-bg", 4.5), ("accent-fg", "surface", 4.5),
         ("border-control", "surface", 3.0), ("border-control", "bg", 3.0), ("focus", "surface", 3.0), ("focus", "bg", 3.0)]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_text_pair_passes_its_bar(css, theme):
    t = _tokens(css)[theme]
    low = [(f, b, round(_ratio(t[f], t[b]), 2)) for f, b, bar in PAIRS if _ratio(t[f], t[b]) < bar]
    assert not low, low


def test_the_inline_styles_in_the_pages_only_go_down():
    pattern = re.compile(r"""style=\\?["']""")
    n = 0
    for name in os.listdir(STATIC):
        if name.endswith((".html", ".js")):
            n += len(pattern.findall(read(name)))
    assert n <= STYLE_ATTRS_MAX, f"{n} inline style= attributes; at most {STYLE_ATTRS_MAX}: give the look a class in rw.css"


def browser_boxes() -> list[str]:
    """Every call of alert, confirm or prompt in the pages' scripts (comments left out), as file:line."""
    call = re.compile(r"(?<![\w.$])(?:window\.)?(alert|confirm|prompt)\s*\(")
    hits = []
    for name in sorted(os.listdir(STATIC)):
        if not name.endswith((".html", ".js")):
            continue
        for n, line in enumerate(read(name).splitlines(), 1):
            code = line if line.lstrip().startswith("<") else line.split("//")[0]
            if call.search(code):
                hits.append(f"{name}:{n}")
    return hits


def test_no_page_opens_a_browser_box():
    """A question is asked beside the button that asked it (RWShell.ask, askText) and a message is a toast (RWShell.say):
    a browser box stops the page, takes none of its words or look, and some browsers stop showing it. The count can only
    go down; it is 0."""
    hits = browser_boxes()
    assert len(hits) <= BROWSER_BOXES_MAX, hits


def test_the_shell_offers_the_inline_question_on_every_page():
    js = read("shell.js")
    for name in ("say", "ask", "askText"):
        assert re.search(rf"function {name}\(", js), name
    assert "say, ask, askText" in js                              # exported on RWShell
    for page in SHELLS:                                          # every page loads the shell before its own scripts
        html = read(page)
        assert html.index("/static/shell.js") < min(i for i in (html.find("/static/" + x) for x in ("upload.js", "swing.js", "features.js", "desk.js")) if i >= 0)
    assert ".inline-ask" in read("rw.css")
