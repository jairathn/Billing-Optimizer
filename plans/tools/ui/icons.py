"""RemitWatch's icon set: plain 24 px stroke icons drawn for this design (no third-party set, nothing to license).

  python3 plans/tools/ui/icons.py --write     rewrites the icon block of remitwatch/static/rw.css (between its @icons
                                              markers) and the sprite remitwatch/static/icons.svg, from ICONS below
  python3 plans/tools/ui/icons.py --check     exits 1 if either file differs from what --write would write
Pages draw an icon from the sprite: <svg class="i"><use href="/static/icons.svg#i-today"/></svg>. The stylesheet draws
the icons in CSS_ICONS itself, as masks that take the text color (kinds of work, states, statuses, hints, copy marks).
Change an icon here, run --write, and commit both files.
"""
import pathlib
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[3]
CSS = ROOT / 'remitwatch' / 'static' / 'rw.css'
SPRITE = ROOT / 'remitwatch' / 'static' / 'icons.svg'
START, END = '/* @icons:start: written by plans/tools/ui/icons.py --write; do not edit by hand */', '/* @icons:end */'

ICONS = {
    # places
    'today':    '<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9v11.5h13V9"/><path d="M10 20.5v-5.5h4v5.5"/>',
    'work':     '<path d="M3 7.5 12 3l9 4.5-9 4.5z"/><path d="m3 12 9 4.5 9-4.5"/><path d="m3 16.5 9 4.5 9-4.5"/>',
    'fax':      '<path d="M7 8V3.5h10V8"/><rect x="3" y="8" width="18" height="9" rx="2"/><path d="M7 14h10v6.5H7z"/><path d="M17.5 11h.01"/>',
    'results':  '<path d="M3.5 3.5v17h17"/><path d="m7.5 15 4-4.5 3 3 5-6"/>',
    'wins':     '<path d="M9.5 18h5"/><path d="M10.5 21.5h3"/><path d="M12 2.5a6.5 6.5 0 0 0-3.8 11.8c.5.4.8 1 .8 1.6V16h6v-.1c0-.6.3-1.2.8-1.6A6.5 6.5 0 0 0 12 2.5z"/>',
    'payers':   '<path d="M3 21h18"/><path d="M5 21V8.5l7-5 7 5V21"/><path d="M9.5 21v-6h5v6"/><path d="M9.5 11h5"/>',
    'people':   '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20.5a6.5 6.5 0 0 1 13 0"/><path d="M15.5 4.7a3.5 3.5 0 0 1 0 6.6"/><path d="M18 14.6a6.5 6.5 0 0 1 3.5 5.9"/>',
    'health':   '<path d="M21.5 12h-4l-3 8.5-5-17-3 8.5h-4"/>',
    'policy':   '<path d="M12 21.5s7.5-3.6 7.5-9.5V5.5L12 2.5l-7.5 3V12c0 5.9 7.5 9.5 7.5 9.5z"/><path d="m8.8 12 2.2 2.2 4.2-4.4"/>',
    'upload':   '<path d="M12 15.5V3.5"/><path d="m7 8.5 5-5 5 5"/><path d="M3.5 15v3.5a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2V15"/>',
    'setup':    '<path d="M4 6.5h9"/><path d="M17 6.5h3"/><circle cx="15" cy="6.5" r="2"/><path d="M4 12h3"/><path d="M11 12h9"/><circle cx="9" cy="12" r="2"/><path d="M4 17.5h11"/><path d="M19 17.5h1"/><circle cx="17" cy="17.5" r="2"/>',
    'lock':     '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    'unlock':   '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 7.7-1.5"/>',
    'calc':     '<rect x="5" y="2.5" width="14" height="19" rx="2.5"/><path d="M8.5 6.5h7"/><path d="M8.5 11h.01M12 11h.01M15.5 11h.01M8.5 14.5h.01M12 14.5h.01M15.5 14.5h.01M8.5 18h.01M12 18h.01M15.5 18h.01"/>',
    'provider': '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
    'grid':     '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
    'database': '<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v13c0 1.7 3.6 3 8 3s8-1.3 8-3v-13"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    # header and controls
    'search':   '<circle cx="11" cy="11" r="7"/><path d="m20.5 20.5-4.5-4.5"/>',
    'bell':     '<path d="M6 8.5a6 6 0 0 1 12 0c0 6.5 2.5 8.5 2.5 8.5h-17S6 15 6 8.5"/><path d="M10.3 21a2 2 0 0 0 3.4 0"/>',
    'help':     '<circle cx="12" cy="12" r="9.5"/><path d="M9.3 9.2a2.8 2.8 0 0 1 5.4 1c0 1.9-2.7 2.6-2.7 2.6"/><path d="M12 17h.01"/>',
    'sun':      '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
    'moon':     '<path d="M20.5 13.3A8.5 8.5 0 1 1 10.7 3.5a6.6 6.6 0 0 0 9.8 9.8z"/>',
    'chev-l':   '<path d="m15 18-6-6 6-6"/>',
    'chev-r':   '<path d="m9 18 6-6-6-6"/>',
    'chev-d':   '<path d="m6 9 6 6 6-6"/>',
    'chev-u':   '<path d="m6 15 6-6 6 6"/>',
    'x':        '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    'check':    '<path d="M20 6.5 9.5 17 4 11.5"/>',
    'copy':     '<rect x="8.5" y="8.5" width="12.5" height="12.5" rx="2.5"/><path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>',
    'external': '<path d="M14 3.5h6.5V10"/><path d="M10 14 20.5 3.5"/><path d="M18.5 13.5v5a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2h5"/>',
    'more':     '<path d="M5 12h.01M12 12h.01M19 12h.01"/>',
    'info':     '<circle cx="12" cy="12" r="9.5"/><path d="M12 16.5v-5"/><path d="M12 8h.01"/>',
    'alert':    '<path d="M10.3 3.9 2.1 18a2 2 0 0 0 1.7 3h16.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4"/><path d="M12 17h.01"/>',
    'stop':     '<path d="M8 2.5h8L21.5 8v8L16 21.5H8L2.5 16V8z"/><path d="M12 8v4.5"/><path d="M12 16h.01"/>',
    'clock':    '<circle cx="12" cy="12" r="9.5"/><path d="M12 7v5l3.5 2"/>',
    'refresh':  '<path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1L20.5 8.5"/><path d="M20.5 3.5v5h-5"/>',
    'filter':   '<path d="M3.5 5h17l-6.5 7.5v6l-4 2v-8z"/>',
    'sort':     '<path d="M7 4v16"/><path d="m3.5 16.5 3.5 3.5 3.5-3.5"/><path d="M14 6h7M14 11h5M14 16h3"/>',
    'download': '<path d="M12 3.5v12"/><path d="m7 10.5 5 5 5-5"/><path d="M3.5 20.5h17"/>',
    'arrow-r':  '<path d="M4.5 12h15"/><path d="m13.5 6 6 6-6 6"/>',
    'menu':     '<path d="M4 6.5h16M4 12h16M4 17.5h16"/>',
    'keyboard': '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M7.5 14h9"/>',
    'mail':     '<rect x="2.5" y="4.5" width="19" height="15" rx="2.5"/><path d="m3 7 9 6 9-6"/>',
    'globe':    '<circle cx="12" cy="12" r="9.5"/><path d="M2.5 12h19"/><path d="M12 2.5a14 14 0 0 1 0 19"/><path d="M12 2.5a14 14 0 0 0 0 19"/>',
    'printer':  '<path d="M7 9V3.5h10V9"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v6.5H7z"/>',
    'send':     '<path d="M21.5 2.5 10.5 13.5"/><path d="M21.5 2.5 15 21.5l-4.5-8-8-4.5z"/>',
    'undo':     '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    'link':     '<path d="M10 13.5a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10.5a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
    'circle-check': '<circle cx="12" cy="12" r="9.5"/><path d="m8.2 12.2 2.6 2.6 5-5.3"/>',
    'minus':    '<path d="M5 12h14"/>',
    'monitor':  '<rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8 20.5h8"/><path d="M12 17v3.5"/>',
    'sidebar':  '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9.5 4v16"/>',
    'command':  '<path d="M9 6.5a2.5 2.5 0 1 0-2.5 2.5H17.5a2.5 2.5 0 1 0-2.5-2.5v11a2.5 2.5 0 1 0 2.5-2.5H6.5A2.5 2.5 0 1 0 9 17.5z"/>',
    'corner':   '<path d="M9 10 4 15l5 5"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/>',
    'inbox':    '<path d="M21.5 13h-5.5l-2 3h-4l-2-3H2.5"/><path d="M5.6 5.2 2.5 13v5.5a2 2 0 0 0 2 2h15a2 2 0 0 0 2-2V13l-3.1-7.8a2 2 0 0 0-1.9-1.2H7.5a2 2 0 0 0-1.9 1.2z"/>',
    'file':     '<path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5"/>',
    # sign out (the sidebar's foot): a door frame with an arrow leaving it
    'sign-out': '<path d="M9.5 20.5h-4a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2h4"/><path d="m15.5 16.5 4.5-4.5-4.5-4.5"/><path d="M20 12H9"/>',
    # a person's status (what they recorded): a mark and a word, never a filled pill
    'st-open':   '<circle cx="12" cy="12" r="8"/>',
    'st-half':   '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="black" stroke="none"/>',
    # the state marks (color is never the only sign of a state)
    's-now':     '<circle cx="12" cy="12" r="9.5"/><path d="M8 12h7.5"/><path d="m12 8.5 3.5 3.5-3.5 3.5"/>',
    's-wait':    '<circle cx="12" cy="12" r="9.5"/><path d="M12 7v5l3.5 2"/>',
    's-done':    '<circle cx="12" cy="12" r="9.5"/><path d="m8.2 12.2 2.6 2.6 5-5.3"/>',
    's-risk':    '<path d="M10.3 3.9 2.1 18a2 2 0 0 0 1.7 3h16.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4"/><path d="M12 17h.01"/>',
    's-blind':   '<path d="M3 3l18 18"/><path d="M10.6 5.1c.5-.1.9-.1 1.4-.1 6.4 0 10 7 10 7a17.4 17.4 0 0 1-3.2 4.1"/><path d="M6.6 6.6A17.5 17.5 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    's-quiet':   '<circle cx="12" cy="12" r="9.5"/><path d="M8 12h8"/>',
    's-expired': '<path d="M6 2.5h12"/><path d="M6 21.5h12"/><path d="M7 2.5v3.6a5 5 0 0 0 2 4L12 12l3-1.9a5 5 0 0 0 2-4V2.5"/><path d="M7 21.5v-3.6a5 5 0 0 1 2-4L12 12l3 1.9a5 5 0 0 1 2 4v3.6"/>',
    # kinds of work: neutral labels told apart by the icon (color is kept for state)
    'k-fix':    '<path d="M14.6 6.4a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
    'k-note':   '<path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5"/><path d="M8.5 13h7M8.5 17h5"/>',
    'k-chase':  '<path d="M21.5 16.6v2.9a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 1.6 3.7a2 2 0 0 1 2-2.2h2.9a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L7.5 9.3a16 16 0 0 0 6 6l1.2-1.2a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2.1z"/>',
    'k-auth':   '<circle cx="8" cy="15.5" r="5"/><path d="m11.6 12 9.4-9.4"/><path d="m16 7.5 3 3"/><path d="m18.5 5 2 2"/>',
    'k-rule':   '<path d="M12 3.5v17"/><path d="M7.5 20.5h9"/><path d="M4.5 7.5h15"/><path d="m4.5 7.5-3 6.5a3.2 3.2 0 0 0 6 0z"/><path d="m19.5 7.5-3 6.5a3.2 3.2 0 0 0 6 0z"/>',
    'k-rate':   '<path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
    'k-check':  '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    'k-bill':   '<path d="M5 2.5v19l2.5-1.5 2.5 1.5 2-1.5 2 1.5 2.5-1.5 2.5 1.5v-19l-2.5 1.5-2.5-1.5-2 1.5-2-1.5-2.5 1.5z"/><path d="M9 8.5h6M9 12.5h6"/>',
    'k-post':   '<path d="M21.5 13h-5.5l-2 3h-4l-2-3H2.5"/><path d="M5.6 5.2 2.5 13v5.5a2 2 0 0 0 2 2h15a2 2 0 0 0 2-2V13l-3.1-7.8a2 2 0 0 0-1.9-1.2H7.5a2 2 0 0 0-1.9 1.2z"/>',
}

# the icons the stylesheet draws itself (as masks, so they take the text color)
CSS_ICONS = ['k-fix', 'k-note', 'k-chase', 'k-auth', 'k-rule', 'k-rate', 'k-check', 'k-bill', 'k-post',
             's-now', 's-wait', 's-done', 's-risk', 's-blind', 's-quiet', 's-expired',
             'st-open', 'st-half', 'arrow-r', 'x', 'minus',
             'info', 'chev-d', 'chev-r', 'check', 'copy', 'search', 'alert', 'stop', 'clock', 'circle-check', 'external', 'lock']


def svg(body, stroke='currentColor', sw='1.75'):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{stroke}" '
            f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


def data_uri(body, stroke='black', sw='2'):
    s = svg(body, stroke=stroke, sw=sw).replace('"', "'")
    return 'url("data:image/svg+xml,' + urllib.parse.quote(s, safe=" '=/:,.-") + '")'


def sprite():
    out = ['<svg xmlns="http://www.w3.org/2000/svg">']
    for k, b in ICONS.items():
        out.append(f'<symbol id="i-{k}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
                   f'stroke-linecap="round" stroke-linejoin="round">{b.replace(chr(34) + "black" + chr(34), chr(34) + "currentColor" + chr(34))}</symbol>')
    out.append('</svg>')
    return '\n'.join(out)


def css():
    out = [':root {']
    for k in CSS_ICONS:
        out.append(f'  --i-{k}: {data_uri(ICONS[k])};')
    # background images (not masks), so they carry their own gray: it reads on both themes (3.3:1 light, 5.4:1 dark)
    out.append(f"  --i-select: {data_uri(ICONS['chev-d'], stroke='#8a929f')};")
    out.append(f"  --i-search-gray: {data_uri(ICONS['search'], stroke='#8a929f')};")
    out.append('}')
    return '\n'.join(out)


def css_block():
    return START + '\n' + css() + '\n' + END


def written():
    """The two files as --write would leave them: (rw.css text, icons.svg text)."""
    text = CSS.read_text()
    a, b = text.index(START), text.index(END) + len(END)
    return text[:a] + css_block() + text[b:], sprite() + '\n'


if __name__ == '__main__':
    arg = (sys.argv[1:] or ['--check'])[0]
    css_text, svg_text = written()
    if arg == '--write':
        CSS.write_text(css_text)
        SPRITE.write_text(svg_text)
        print(f'wrote {CSS.relative_to(ROOT)} and {SPRITE.relative_to(ROOT)}')
    elif arg == '--check':
        stale = [str(f.relative_to(ROOT)) for f, t in ((CSS, css_text), (SPRITE, svg_text)) if not f.exists() or f.read_text() != t]
        print('stale: ' + ', '.join(stale) if stale else 'icons up to date')
        sys.exit(1 if stale else 0)
    else:
        print(sprite() if arg == 'sprite' else css())
