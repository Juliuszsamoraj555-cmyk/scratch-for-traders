/* ============================================================
   INSTRUMENT MARKS (2026-09-01)
   ============================================================
   Two overlapping currency discs per instrument, base currency in front -
   the same convention a broker's own instrument list uses. Shared by
   index.html's homepage teaser and strategy-of-the-week.html, and
   available to strategy-detail.html / my-purchases.html when they want it.

   Why a shared file rather than another copy: this project tolerates
   duplicating small self-contained blocks across a couple of consumers
   (see assets/marketplace-auth.js's own header), but that file also set
   the threshold - "if a third page ever needs it, that's the point to
   actually factor it into one shared file". Four pages want these marks,
   and a flag redrawn slightly differently per page would be a visible
   inconsistency, not just duplicated code.

   Every disc is hand-drawn inline SVG rather than pulled from a flag-icon
   package: nothing on this site loads from a CDN at runtime (see
   assets/vendor/ for the same decision about Blockly and Tailwind), and
   these are simple enough that a dependency would cost more than it saves.

   Each is drawn edge-to-edge in a 24x24 box and clipped to a circle by
   .im-disc's border-radius + overflow:hidden, NOT by an SVG <clipPath> -
   clipPath needs a document-unique id per instance, which collides the
   moment two marks render on one page.

   CONSUMING PAGES MUST DEFINE .im-pair / .im-disc THEMSELVES (size,
   overlap, ring) - deliberately not injected from here, since the two
   pages draw them at different sizes. Copy the block from either
   consumer; it's ~10 lines.
   ============================================================ */

const CURRENCY_DISCS = {
  USD: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#f9fafb"/>
    <g fill="#b22234"><rect y="0" width="24" height="1.85"/><rect y="3.7" width="24" height="1.85"/><rect y="7.4" width="24" height="1.85"/><rect y="11.1" width="24" height="1.85"/><rect y="14.8" width="24" height="1.85"/><rect y="18.5" width="24" height="1.85"/><rect y="22.2" width="24" height="1.85"/></g>
    <rect width="10" height="12.95" fill="#3c3b6e"/>
  </svg>`,
  JPY: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#f9fafb"/>
    <circle cx="12" cy="12" r="6" fill="#bc002d"/>
  </svg>`,
  CAD: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#f9fafb"/>
    <rect width="6" height="24" fill="#d52b1e"/><rect x="18" width="6" height="24" fill="#d52b1e"/>
    <path d="M12 5.2l1.5 2.85 1.7-.45-.5 2.45 2.1.3-1.5 1.5 2.2 1.65-2.8.5.25 1.3-2.6-.35.15 2.9h-1l.15-2.9-2.6.35.25-1.3-2.8-.5 2.2-1.65-1.5-1.5 2.1-.3-.5-2.45 1.7.45z" fill="#d52b1e"/>
  </svg>`,
  AUD: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#00247d"/>
    <path d="M0 0L12 12M12 0L0 12" stroke="#f9fafb" stroke-width="2.2"/>
    <path d="M6 0V12M0 6H12" stroke="#f9fafb" stroke-width="3.4"/>
    <path d="M6 0V12M0 6H12" stroke="#cf142b" stroke-width="1.7"/>
    <circle cx="6" cy="17.5" r="2" fill="#f9fafb"/>
    <circle cx="17.2" cy="6.4" r="1.15" fill="#f9fafb"/>
    <circle cx="20" cy="12" r="1.15" fill="#f9fafb"/>
    <circle cx="16.4" cy="17.4" r="1.15" fill="#f9fafb"/>
    <circle cx="20.8" cy="18.2" r="0.8" fill="#f9fafb"/>
  </svg>`,
  // Gold gets a bar stack instead of a flag, same as a broker's own list.
  // Sized to sit fully INSIDE the circular clip - a wider earlier stack
  // had its bottom corners sliced off by the disc's radius.
  XAU: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#a16207"/>
    <path d="M9.2 9.4h5.6l1 3.6H8.2z" fill="#fef3c7"/>
    <path d="M5.4 14h5.6l1 3.6H4.4z" fill="#fde68a"/>
    <path d="M13 14h5.6l1 3.6H12z" fill="#fde68a"/>
  </svg>`,
  // Silver, for a future XAGUSD listing - same bar-stack idiom as gold so
  // the two read as a pair rather than two unrelated drawings.
  XAG: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#4b5563"/>
    <path d="M9.2 9.4h5.6l1 3.6H8.2z" fill="#f9fafb"/>
    <path d="M5.4 14h5.6l1 3.6H4.4z" fill="#d1d5db"/>
    <path d="M13 14h5.6l1 3.6H12z" fill="#d1d5db"/>
  </svg>`,
  EUR: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#003399"/>
    <g fill="#ffcc00"><circle cx="12" cy="5.6" r="1.1"/><circle cx="12" cy="18.4" r="1.1"/><circle cx="5.6" cy="12" r="1.1"/><circle cx="18.4" cy="12" r="1.1"/><circle cx="7.5" cy="7.5" r="1.1"/><circle cx="16.5" cy="7.5" r="1.1"/><circle cx="7.5" cy="16.5" r="1.1"/><circle cx="16.5" cy="16.5" r="1.1"/></g>
  </svg>`,
  GBP: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#012169"/>
    <path d="M0 0L24 24M24 0L0 24" stroke="#f9fafb" stroke-width="4.4"/>
    <path d="M0 0L24 24M24 0L0 24" stroke="#c8102e" stroke-width="2.4"/>
    <path d="M12 0V24M0 12H24" stroke="#f9fafb" stroke-width="7"/>
    <path d="M12 0V24M0 12H24" stroke="#c8102e" stroke-width="4"/>
  </svg>`,
  CHF: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#d52b1e"/>
    <path d="M10 5h4v5h5v4h-5v5h-4v-5H5v-4h5z" fill="#f9fafb"/>
  </svg>`,
  NZD: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#00247d"/>
    <path d="M0 0L12 12M12 0L0 12" stroke="#f9fafb" stroke-width="2.2"/>
    <path d="M6 0V12M0 6H12" stroke="#f9fafb" stroke-width="3.4"/>
    <path d="M6 0V12M0 6H12" stroke="#cf142b" stroke-width="1.7"/>
    <circle cx="17.4" cy="7" r="1.3" fill="#cf142b"/>
    <circle cx="20" cy="12.4" r="1.3" fill="#cf142b"/>
    <circle cx="16.4" cy="17.6" r="1.3" fill="#cf142b"/>
    <circle cx="21" cy="18" r="1" fill="#cf142b"/>
  </svg>`,
};

// Unknown code (a future instrument with no disc drawn yet) falls back to
// its 3-letter code on a neutral disc rather than rendering nothing.
function currencyDisc(code) {
  return CURRENCY_DISCS[code] || `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect width="24" height="24" fill="#374151"/>
    <text x="12" y="15.5" fill="#e5e7eb" font-size="8" font-weight="700" font-family="ui-sans-serif,system-ui" text-anchor="middle">${code}</text>
  </svg>`;
}

// extraClass lets a caller size one instance differently (e.g. the free
// strategy's hero card draws a bigger mark than the list rows do) without
// this file needing to know about either page's sizing scheme.
function instrumentMark(symbol, extraClass) {
  const base = symbol.slice(0, 3);
  const quote = symbol.slice(3, 6);
  return `<span class="im-pair ${extraClass || ''}" aria-hidden="true">
    <span class="im-disc im-disc--back">${currencyDisc(quote)}</span>
    <span class="im-disc im-disc--front">${currencyDisc(base)}</span>
  </span>`;
}
