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
`article-2.html`).

Fastest way: copy an existing article (`blog/what-is-algorithmic-trading.html`
is the shortest) and replace its content — the `<head>` block, nav, and
footer are identical boilerplate across every article on purpose.

## 3. Required per-article checklist

Everything in this list is copy-pasteable from an existing article,
just needs updating with the new content:

- [ ] `<title>` — under ~60 characters, format: `<Headline> - Scratch for Traders`
- [ ] `<meta name="description">` — under ~155 characters, states what the reader gets
- [ ] `<link rel="canonical">` — matches the final URL once a domain exists
- [ ] `og:type`, `og:title`, `og:description`, `og:url`
- [ ] `Article` JSON-LD block (`headline`, `description`, `datePublished` — use the real publish date)
- [ ] `<h1>` — should closely match `<title>` and the target search phrase
- [ ] Body: 400-800 words, plain language, short paragraphs, at least one `<h2>` subheading break
- [ ] A CTA at the bottom linking to `../index_1.html` (every article should end by sending the reader to actually build something)
- [ ] "&larr; Back to all articles" link to `../index.html#blog`

## 4. Register the article in three places

A new HTML file alone is invisible to Google until it's linked from
somewhere and listed in the sitemap:

1. **`blog/index.html`** — add a new card (copy the row-format markup:
   graphic + category tag + title + excerpt + "Read article" link).
2. **`index.html`'s `#blog` section** — same card markup. **Once there
   are more than 3 articles**, stop growing this list indefinitely:
   switch it to showing only the 3 most recent, with a "View all
   articles &rarr;" link to `blog/index.html` instead. (Not needed yet
   at 3 articles — but don't let the homepage section become a scroll of
   15 cards.)
3. **`sitemap.xml`** — add a `<url>` entry (`changefreq: yearly`,
   `priority: 0.6`, matching the pattern already there).

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
