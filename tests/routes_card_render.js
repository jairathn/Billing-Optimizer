// The route card as the drawer draws it (static/features.js xpRouteHtml and its helpers, the code between `const RC_CH = `
// and the comment that opens xpRouteBind), for tests/test_routes_review.py. Usage:
//   node routes_card_render.js <features.js> <card.json>
// card.json is routes.card's answer, with `_buttons` (the next step's buttons) and `_item` (the item's own fields) when the
// test needs them. Prints the card's HTML. Invented data only.
const fs = require('fs'), vm = require('vm');
const [, , featuresPath, cardPath] = process.argv;
const src = fs.readFileSync(featuresPath, 'utf8');
const start = src.indexOf('const RC_CH = ');
const end = src.indexOf("// the card's buttons:");
if (start < 0 || end < 0 || end < start) { console.error('route card code not found in features.js'); process.exit(2); }
const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const ctx = { esc, longDate: d => String(d), closesText: () => ({ cls: '', text: 'open' }), xpEffective: (f, x) => x, console };
vm.createContext(ctx);
vm.runInContext(src.slice(start, end) + '\nthis.xpRouteHtml = xpRouteHtml;', ctx);
const card = JSON.parse(fs.readFileSync(cardPath, 'utf8'));
const x = { route_card: card, explain: { next_step: { route: card.route, buttons: card._buttons || [] } } };
process.stdout.write(ctx.xpRouteHtml(Object.assign({ work_state: 'work_now' }, card._item || {}), x, { packets: [] }));
