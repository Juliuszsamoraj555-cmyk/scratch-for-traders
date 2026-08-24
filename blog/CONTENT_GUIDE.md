# Adding a new blog article — the repeatable process

The blog exists for one reason: `index_1.html` (the Blockly canvas) has
nothing for Google to read, so organic search traffic has to come through
here instead. Every step below exists in service of that goal — skipping
one doesn't break anything today, but it quietly caps how much a new
article actually helps search visibility.

## 1. Pick a topic that matches a real search query

The best-performing pattern so far (see the 3 existing articles) is a
title that's *itself* a question or phrase someone would actually type
into Google — "What Is Algorithmic Trading?", "Stop Loss vs. Take
Profit" — rather than a clever/branded headline. Before writing, ask:
what would a trader searching for this literally type?

Good sources for topic ideas: the FAQ section on `index.html` (each
question is already a validated search-shaped phrase), MQL5/cTrader
community forum threads, and any question a real user has asked about
the product.

## 2. Create the file

Path: `blog/<kebab-case-slug>.html`, where the slug is close to the
target search phrase (`stop-loss-take-profit-guide.html`, not
`article-2.html`). **The on-disk filename still ends in `.html`** - only
the *served URL* is clean (`/blog/<slug>/`, no extension, via the
rewrite/redirect rules in `render.yaml`, see docs/HANDOFF.md Session 6).
Don't rename the file itself; a new slug just needs a matching pair of
`routes` entries added to `render.yaml` (copy an existing slug's
redirect + rewrite pair) before the clean URL will resolve in production.

Fastest way: copy an existing article (`blog/what-is-algorithmic-trading.html`
is the shortest) and replace its content — the `<head>` block, nav, and
footer are identical boilerplate across every article on purpose.

## 3. Required per-article checklist

Everything in this list is copy-pasteable from an existing article,
just needs updating with the new content:

- [ ] `<title>` — under ~60 characters, format: `<Headline> - AlgoPuzzle`
- [ ] `<meta name="description">` — under ~155 characters, states what the reader gets
- [ ] `<link rel="canonical">` — the clean URL, `https://algopuzzle.com/blog/<slug>/` (trailing slash, no `.html`)
- [ ] `og:type`, `og:title`, `og:description`, `og:url` (same clean form as canonical), `og:image`
- [ ] `Article` JSON-LD block: `headline`, `description`, `author`,
      `publisher`, `datePublished` **and** `dateModified` (same value
      unless the content is actually later edited), `image`
      (`https://algopuzzle.com/assets/og-image.png`), and
      `mainEntityOfPage` (`{ "@type": "WebPage", "@id": "<canonical URL>" }`)
- [ ] `BreadcrumbList` JSON-LD block right after the `Article` one — 3
      items: Home (`/`), Blog (`/blog/`), then this article's title +
      clean URL. Copy an existing article's block, it's boilerplate
      except the title/URL on item 3.
- [ ] `<h1>` — should closely match `<title>` and the target search phrase
- [ ] Body: 400-800 words, plain language, short paragraphs, at least one `<h2>` subheading break
- [ ] A CTA at the bottom linking to `/builder/` (every article should end by sending the reader to actually build something) — same URL for the header's "Open the Builder" button and the logo link (`/`)
- [ ] "&larr; Back to all articles" link to `/#blog`
- [ ] Any cross-link to another article uses its clean URL (`/blog/<other-slug>/`), not a relative `<other-slug>.html`

## 3b. LLMO — writing so an AI answer engine can actually quote it

Google isn't the only thing reading these anymore - ChatGPT, Claude, and
Perplexity increasingly answer trading questions directly, citing (or
not citing) a source in the process. This section is what changed as a
result, effective for every article going forward (started 2026-08-20 -
see `how-to-build-your-first-algorithmic-strategy.html` for a worked
example. Existing older articles are intentionally NOT being retrofitted
to this - it's a going-forward standard, not a backfill project):

- [ ] **First paragraph answers the question directly, in ~150-200
      words, before anything else.** Not a windup - if the title asks
      "how do I X", paragraph one states the actual answer. This is the
      single highest-leverage change: it's the part most likely to get
      lifted verbatim into an AI answer.
- [ ] **The first 1-2 sentences under every `<h2>` are a standalone,
      quotable claim** - readable and correct even with zero
      surrounding context, because that's exactly how a model extracts
      it.
- [ ] **Any comparison goes in a real `<table>`, never prose.** Models
      extract HTML tables close to verbatim; a comparison written as a
      paragraph has to be paraphrased (and often garbled or dropped) to
      extract the same information. (`article table` /
      `article th, article td` styles are already in the shared
      `<style>` block - copy them into a new article's `<head>` if
      they're not there yet.)
- [ ] **Step-by-step content uses a real numbered structure** (`<h2>`
      per step or an `<ol>`), not narrative prose describing steps -
      same extractability reasoning as tables.
- [ ] **Add a `FAQPage` JSON-LD block** alongside the `Article` one, 3-5
      questions, mirroring an actual "Common questions" section in the
      body (same paired pattern `index.html` already uses for its own
      FAQ). Q&A pairs are one of the formats AI answer engines lean on
      most - phrase questions the way someone would actually type them.
- [ ] **Cover the natural-language variants of the target phrase**, not
      just one exact keyword - "algo strategy builder", "algorithmic
      trading strategy", "no-code EA creator", etc. - somewhere in the
      body copy. AI answer engines match semantically, not on exact
      keyword strings, so synonym coverage matters more here than it
      does for classic SEO.

Two content *types* worth deliberately seeding into the topic queue
because they get cited disproportionately often, per 2026 GEO data
(listicles alone account for the majority of citations across a
400M-citation study):
- **Ranked/comparison roundups** ("best no-code strategy builders",
  "MT5 vs MT4 vs cTrader for automation") - written honestly, including
  real alternatives, not just self-promotion dressed as a ranking. A
  transparently biased "top 10" reads as untrustworthy to a model just
  as it would to a person, and undermines citation-worthiness rather
  than helping it.
- **Direct how-to tutorials** matching a real query shape ("how to
  build X"), per the Step 1/2/3 pattern in section 3b above.

## 4. Register the article in four places

A new HTML file alone is invisible to Google (or an AI crawler) until
it's linked from somewhere and listed in the sitemap:

1. **`blog/index.html`** — add a new card **at the top** of the list
   (newest-first order - see the comment above the card list there),
   `href="/blog/<slug>/"` (clean URL, not the `.html` filename).
   Give it a `<time datetime="YYYY-MM-DD">` next to the category tag,
   and move the `New` badge (`absolute top-3 ... style="right:
   0.75rem"` span, plus `relative` on the `<a>`) from whichever card
   currently has it onto the new one - only the single newest post
   keeps the badge, never two at once.
2. **`index.html`'s `#blog` section** — same card markup (using `<h3>`
   instead of `<h2>` there), same `href="/blog/<slug>/"` and
   badge-and-date treatment. This section shows only the 3 most recent
   posts, newest first - add the new one at the top and delete
   whichever card is now 4th, don't let it grow past 3.
3. **`sitemap.xml`** — add a `<url>` entry at the top of the blog
   entries (`<loc>https://algopuzzle.com/blog/<slug>/</loc>`,
   `changefreq: yearly`, `priority: 0.6`, matching the pattern already
   there), and bump the `<lastmod>` on both the `/` and `/blog/`
   entries to today's date.
4. **`render.yaml`** — add a matching redirect + rewrite pair for the
   new slug under `algopuzzle-frontend`'s `routes` (copy any existing
   slug's 3 entries - the `.html`-to-clean-URL redirect, the
   no-trailing-slash-to-trailing-slash redirect, and the rewrite that
   actually serves the file). Without this, the clean URL 404s in
   production even though the file exists on disk.

## 5. Internal-link it from somewhere relevant

Not just a nice-to-have — internal links are a real ranking signal, and
they help readers actually discover it. When a new article is
topically related to an existing one, add one contextual sentence-level
link between them (e.g. the indicators article could link to the
stop-loss article when it mentions ATR-based sizing). Don't force it if
there's no natural connection.

## 6. Publish consistently, not in bursts

A slow, steady cadence (even one article every 1-2 weeks) signals an
active, maintained site to Google better than 10 articles published on
day one and then nothing for six months. When in doubt, ship fewer,
better articles on a predictable schedule rather than a pile at once.

## Fastest way to add one going forward

Just ask, with a topic and (ideally) the search phrase it should target,
e.g.: *"write a short blog article about position sizing, targeting
'how to size a forex position'"* — the file, the checklist above, and
registering it in all 3 places is a single, repeatable pass.
