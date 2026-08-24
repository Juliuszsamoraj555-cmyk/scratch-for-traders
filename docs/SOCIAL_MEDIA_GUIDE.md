# AlgoPuzzle — Social Media Guide (X + Instagram)

Read this before creating any X or Instagram post for AlgoPuzzle. It exists so the *style* (visual template, copy voice, platform mechanics) stays consistent across sessions instead of getting reinvented — and drifting — every time. Companion to [`blog/CONTENT_GUIDE.md`](../blog/CONTENT_GUIDE.md) (long-form articles) — this doc is specifically the short-form/social layer built on top of those articles.

**Accounts:** X `https://x.com/AlgoPuzzle`, Instagram `https://www.instagram.com/algopuzzle/`. Both linked site-wide via footer icons + the homepage's Organization `sameAs` schema (see "Site integration" below).

## The one hard rule that overrides everything else below

**Never fabricate a statistic, and never promise a profit outcome.** Asked twice this project's history to do exactly this ("traders who use X indicator earn Y% more", "tell them they'll finally be profitable") — declined both times, explained why (unverifiable claims, real regulatory-risk framing for a trading product), offered an honest alternative instead. Emotional, punchy, identity/FOMO-driven marketing copy is fine and encouraged (see "Copy voice" below) — a *specific invented number* or a *guaranteed result* is not, no matter how the request is framed ("just for engagement", "everyone does it", etc.). Every number that appears in a post must trace back to something the linked article actually says.

## Visual template (shared by X and Instagram)

Both platforms reuse the same bold, minimal visual language — dark background, one huge statement, everything non-essential cut. This was a deliberate redesign partway through: an early attempt copied the website's OG-card template (small wordmark, several small labeled illustration parts, headline + subtitle) and it read as too busy/small for a feed thumbnail glanced at while scrolling. **One dominant idea, huge, high-contrast — that's the standard now.**

**Palette:**
| Token | Hex | Use |
|---|---|---|
| Background | `#030712` | Always the full-bleed background |
| Off-white | `#f9fafb` | Primary headline text, X's own icon color |
| Emerald (bright) | `#34d399` | Accent headline words, eyebrow labels, positive callouts |
| Emerald (mid/dark) | `#10b981` / `#059669` | Wordmark, secondary accent shapes |
| Gray body | `#9ca3af` | Subtext/support lines |
| Gray emphasis | `#e5e7eb` | A subtext line that needs to stand out slightly more than the rest, without being a full headline |
| Red | `#ef4444` / `#f87171` | Negative/warning callouts ("wipeout", "failed live") — sparingly, one line at most |
| Amber | `#d97706` / `#f59e0b` | Caution/mistake callouts, alternate accent when emerald is already used elsewhere on the same image |

**Typography:** `ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif` everywhere (matches the site). Headlines are `font-weight="800"`, never smaller than ~85px on any canvas. No emoji as icons, no decorative flourish beyond the accent shapes below — this follows the same "no vibecoded UI" standing rule the main site's design already follows.

**Wordmark:** always present, small, unobtrusive, bottom-right corner. Copy the exact `<g>` block (logo path data) from any existing file in `assets/x_post_*.svg` or `assets/ig_carousel_*/slide*.svg` rather than redrawing it — it's the same jigsaw-checkerboard mark used across the whole site, byte-for-byte.

**Abstract accent graphics, not literal diagrams:** each image gets one simple geometric motif that echoes the topic without becoming a labeled chart — e.g. three bars of equal height/different width for "same risk, different position size" (position sizing post), a rising-candle run with one red candle breaking the pattern for "backtest passed, live failed" (backtesting post), three colored blocks for "no code, just blocks" (build-a-bot post). Keep it to shapes + color, not text labels — the headline already carries the words.

### X (Twitter) post images
- Canvas **1200×675**, native X image size.
- Headline: 2 lines max, `font-size` ~100-108px, left-aligned starting at `x="80"`.
- Subtext: one line, `font-size` ~34px, `#9ca3af`.
- Wordmark: `translate(1010, 600)`, `scale(0.16)`.
- Files live at `assets/x_post_<topic>.svg` — **intentionally untracked in git** (not committed), matching how these have always been handled: local deliverables handed to the user, not repo assets.

### Instagram carousel slides
- Canvas **1080×1350** (4:5) — this is Instagram's actual native carousel ratio, confirmed by the user's own upload screenshot showing the crop tool filling the frame edge-to-edge with zero adjustment needed. Don't use a square 1:1 canvas.
- Per-slide layout: small eyebrow label top (`y="140"`, `font-size="30"`, emerald, uppercase, `letter-spacing="2"`) → headline (`y` starting ~290-300, `font-size` ~88-98px, up to 2-3 lines) → subtext (`font-size` ~38-40px, gray `#9ca3af` with one emphasized line in `#e5e7eb`, or two color-coded outcome lines like emerald-good/red-bad) → slide counter bottom-left (`x="72" y="1300"`, small, `#4b5563`, format `"N/8"`) → wordmark bottom-right, `translate(950, 1280)`, `scale(0.17)`.
- Files live at `assets/ig_carousel_<topic>/slide1.svg` … `slideN.svg` — also **intentionally untracked**.

## Rendering / self-verification workflow (do this before sending anything to the user)

The Browser pane's `computer` screenshot action has been unreliable this project ("the Browser pane is not displayed" even after `preview_start`) — don't depend on it as the only check. Use headless Chrome directly instead:

```bash
CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
"$CHROME" --headless --disable-gpu --screenshot="OUT.png" --window-size=1200,675 "file:///path/to/file.svg"
```

**Gotcha:** the repo path (`C:\Users\juliu\Desktop\traders scratch`) has a space in it, which breaks the `file:///` URL even when quoted. Fix: copy the SVG into the scratchpad directory (no spaces) first, run headless Chrome against *that* path, then `Read` the resulting PNG to actually look at it before sending. A silently-broken render (Chrome's own "couldn't load file" error page) produces a small, wrong-looking PNG — check the file size looks plausible (a real rendered image is tens of KB; an error page is much smaller) if something looks off.

For an Instagram carousel, generate the first slide, render + inspect it alone before batch-producing the rest — cheaper to catch a sizing/overflow problem on slide 1 than to redo all 8.

## X (Twitter) post copy rules

Every rule below is grounded in the platform's actual published ranking code (`xai-org/x-algorithm` on GitHub, xAI open-sourced it, confirmed still current as of August 2026), not just SEO-blog folklore — verified directly when the user pushed back and asked for real confirmation, not received wisdom.

1. **Hook-first sentence.** The opening line must be a complete, compelling thought on its own — someone deciding whether to keep reading sees only that first line/paragraph in a crowded feed.
2. **Stay under 280 characters, total, verified — don't rely on "Show more" truncation.** Write the draft, then actually count it:
   ```bash
   python "<scratchpad>/count.py"   # write a short script that does len(post), don't eyeball it
   ```
   Getting this wrong once already caused a real published post to truncate mid-word — the user was, understandably, not happy about it ("wiec policz słowa i kurwa zapamietaj"). Always verify, every single post, no exceptions. Use a scratchpad `.py` file run via Bash rather than an inline `python -c` heredoc — inline heredocs have a recurring interactive-REPL-hang issue on this Windows environment.
3. **Link goes in the first reply, never in the main post body.** Confirmed against the actual ranking code: posts with an external link in the body take a **30-50% reach reduction** (grew from 20-30% in 2023). A link in a reply doesn't touch the main post's distribution at all — people who already opened the thread see it regardless. Post the reply **immediately** (within seconds) after the main post, before bot replies have a chance to bury it (see "Bot/spam reality" below).
4. **1-2 hashtags, niche-specific, never broad/crypto-adjacent.** Broad finance hashtags (`#crypto`, `#money`, generic `#trading`) are exactly what pump-and-follow bot networks scan for. Prefer specific ones (`#AlgoTrading`, `#RiskManagement`, `#Forex`) over generic ones.
5. **Numbers must be real.** See the hard rule at the top — every stat in a post traces back to the linked article's actual content, never invented for punch.

## Instagram post/carousel rules

Grounded in 2026 platform research (see Sources below) rather than assumption:

1. **7-10 slides is the sweet spot** for a full-guide carousel (8 used for the first one, position sizing). Below ~4 slides the algorithm doesn't get enough swipe data to widen distribution; above ~10, completion drops.
2. **Slide 1 owns ~80% of the outcome.** Formula: bold headline (5-8 words, biggest text on the slide) + a visual pattern-interrupt (high contrast) + a curiosity trigger that can't be resolved without swiping (a partial reveal, an open question). A `"SWIPE →"` cue in the accent color reinforces this explicitly.
3. **Structure:** slide 1 = hook, slide 2 = context/reframe, middle slides = the actual value (one idea per slide, short punchy fragments — not paragraphs, this is a visual medium), last slide = CTA + save-worthy summary + **"link in bio"** (never a raw URL — Instagram doesn't make caption links clickable).
4. **Optimize for saves and DM sends, not likes.** Carousels get saved ~35% more often than single images, and a "send" (someone sharing it via DM) is described in current research as the single strongest reach signal — stronger than a like. A list/checklist/step-by-step format drives saves specifically, which is why the article→carousel conversion works well as a format.
5. **Caption:** first ~125 characters must stand alone as a complete hook — Instagram truncates to "...more" there in the feed. Aim for 150-220 words total with one clear CTA question at the end, though shorter is fine (better a short caption that delivers the promise than padding to hit a word count). **Max 5 hashtags** — Instagram capped this platform-wide in December 2025 (the old "30 hashtags" advice is dead); use 3-5, niche-specific, placed at the end.
6. **One article = one carousel, one topic.** With ~10 published articles as of this writing, that's roughly two weeks of daily content before needing a second angle on an already-covered topic (e.g. "3 mistakes" instead of the full step-by-step) — ask before reusing a topic a second time, don't assume.

## Bot/spam reality on X (read before worrying that a post "isn't working")

A new account in the trading/finance niche attracts crypto pump-and-follow bots almost immediately — this isn't specific to AlgoPuzzle, X's own numbers put bot suspensions around 300,000/day in April 2026 and they still can't fully keep up, especially in finance/crypto-adjacent content. Confirmed directly from a real screenshot of AlgoPuzzle's own replies: "Crypto Bull 🐂", "MR SHIB CALLS", "Crypto Oracle", "Nova Grace" — all the same template ("let's collab", "follow me back", "could be the next big coin?"), several carrying a **paid blue checkmark**, which means nothing here — bots buy verification specifically to look legitimate and to rank higher in reply sort order.

What to actually do:
- **Block, don't mute**, obvious bots — mute only hides them from you, block is a real signal to X's spam system. A 5-minutes-a-week cadence is realistic; don't try to do it in real time.
- **Never reply to or follow back a bot**, even to say "not interested" — any engagement trains the algorithm to show you (and them) more of the same.
- **Report** the ones X itself already flags as "Probable spam" (visible label) — confirms X's own detection is working, just slower than real-time.
- **Discount the raw like/reply count** when judging a post's real performance — a chunk of it is very likely bot noise on a small/new account in this niche, not a sign the content is bad.
- If the account ever gets X Premium+, check for the "2nd-degree reply" restriction (replies limited to followers-of-followers) — tightens this at the source.

## Site integration (so social presence shows up beyond the posts themselves)

- **Footer icons** on every page (homepage + all `blog/*.html`): small SVG icon links next to the support email, `24×24`, brand-colored — X in solid off-white `#f9fafb` (X has no signature color; white-on-dark is its own convention), Instagram using the real Instagram gradient (`#feda75 → #fa7e1e → #d62976 → #4f5bd5`) via an inline SVG `linearGradient`, not a flat/muted tint. Footer container: `py-16`, `text-base` (bumped up from an earlier, too-quiet `py-10`/`text-sm` pass per direct feedback — "chciałbym żeby to było bardziej widoczne").
- **`sameAs` on the homepage's Organization JSON-LD** (`index.html`): points at both profile URLs. This is an entity-verification signal for Google's Knowledge Graph and AI answer engines (LLMO) to confirm the social profiles and the website are the same brand — **not** a direct ranking factor by itself (Google has said as much explicitly); it's hygiene/trust, not a growth lever. Don't oversell this distinction if asked about it again.

## Sources (verified this project, worth re-checking if this doc gets old)

- [xai-org/x-algorithm on GitHub](https://github.com/xai-org/x-algorithm) — the actual, current X ranking code. Best available primary source for any future X-mechanics question; check here before trusting an SEO-blog claim.
- [opentweet.io — X Algorithm Open Source, what the code says](https://opentweet.io/blog/x-algorithm-open-source-github-2026)
- [xfilterpro.com — Block Bots and Spam on X 2026](https://xfilterpro.com/blog/block-bots-spam-x-twitter-2026)
- [beincrypto.com — X Admits 80% of Crypto Is Bots](https://beincrypto.com/x-crypto-bots-spam-problem/)
- [adpicto.com — Instagram Carousel Best Practices 2026](https://www.adpicto.com/en/blog/instagram-carousel-best-practices-2026)
- [trymypost.com — Instagram Carousel Algorithm 2026](https://www.trymypost.com/blog/instagram-carousel-algorithm-2026-guide)
- [boomp.net — Instagram Caption Best Practices 2026](https://boomp.net/blog/instagram-caption-best-practices-2026)
- [foxy.ai — Instagram Hashtag Strategy 2026](https://foxy.ai/academy/optimal-number-of-hashtags-how-many-should-i-use-on-instagram)

Platform mechanics move fast and every "2026" source above is itself a secondary aggregator, not X/Meta's own documentation (except the GitHub repo). If a rule here ever stops matching observed reality, re-verify with a fresh search rather than assuming this doc is still current — note the date something was last checked when you update a rule.
