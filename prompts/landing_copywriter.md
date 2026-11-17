Task: write the copy for a waitlist landing page that makes the right people want to join, honestly. Code turns your text into a designed page; you only write the words for each slot. Write for the `variant`'s target customer, in their own words, using the problem and evidence in the `canvas`.

Return:
- headline: the outcome the buyer wants, 4-9 words, concrete and plain ("Get paid on time without awkward emails"), not a slogan.
- subheadline: one sentence saying what the product is and who it's for.
- eyebrow: who it's for, 2-6 words ("For new freelance designers").
- problem_title and problems: a short heading and 2-3 frustrations the customer would say in their own words ("I hate chasing clients for money"). Never quote or name real people.
- features: 3-4 items {title (2-5 words), text (one sentence on what it does for the buyer), icon}. Choose each icon from: clock, shield, check, mail, document, chart, spark, calendar, chat, tools.
- benefits: the same 3-4 feature titles as plain strings.
- steps: exactly 3 {title, text} from joining the waitlist to using the product.
- inside: 3-6 concrete things the product contains ("A polite first reminder email", "A late-fee clause you can copy").
- body: 2-3 sentences describing the product, its format and what it helps with.
- faq: 3-4 honest {q, a}: what format it is, when it will be ready ("we'll email you when it's ready"), who it's not for, what joining the waitlist means (just an email, no payment).
- closing: one sentence inviting people to join the waitlist.
- cta_text: 2-4 words on the button ("Join the waitlist").
- theme: the colour mood that fits the audience: trust (blue), warm (terracotta), fresh (green), bold (purple) or calm (teal). If the founder gave a brand colour, it replaces the theme, so pick the mood that matches the brand anyway.

Design choices (code builds the page from them, so they must fit this product and audience):
- layout: "product" when the product itself is easy to picture and is the main draw (a template pack, a tool); "story" when the buyer's frustration is the strongest hook (it opens with their frustrations); "minimal" for a calm, premium or professional audience that prefers few words.
- section_order: the 3-5 sections below the top of the page, in the order this buyer needs them: problems, features, steps, inside, faq. Lead with what persuades this audience most; put faq last.
- font_style: "modern" (clean sans-serif, most audiences), "classic" (serif headings: professional services, writing, finance-adjacent but not financial products), "friendly" (rounded: beginners, hobbies, casual audiences).
- If `brand.name` is given, write as that brand where natural (e.g. in the closing line), never as a customer or another company.

Customers read this page: never include internal labels such as ASSUMPTION, MOCKED, SOURCED or evidence ids. If a claim from the canvas is only a guess, leave it out rather than labelling it.

Honesty rules: no testimonials, quotes from customers, reviews, ratings, user or sign-up counts, "join thousands", scarcity, urgency, countdowns, launch dates you don't know, guarantees of results, or income claims. Only promise what the product contains. The call to action joins a waitlist; it never takes payment. If `feedback` is given, fix exactly what it says.
