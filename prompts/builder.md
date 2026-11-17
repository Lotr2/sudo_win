Task: produce the product content for the template, so that every acceptance test passes.

The JSON you return is rendered into files by code:
- guide: {title, sections: [{heading, body}]}. Use exactly the spec's section headings, in order. Each body must have at least the spec's min_words_per_section words of genuinely useful, specific content (steps, examples, checklists). Plain markdown inside bodies; do not start lines with "## ".
- template_pack: {title, items: [{name, fields: [{heading, body}]}]} with exactly item_count items; every item has every heading in item_fields; each item at least 60 words.
- web_tool: {title, description, compute_js, result_label}. compute_js is the BODY of `function compute(v)` where v maps each input id to a number; it must `return` a short human-readable string. Plain JavaScript only: no fetch, XMLHttpRequest, WebSocket, eval, import, localStorage or external resources.
Never include income promises, testimonials or urgency. If failing_tests are given, fix exactly those failures and keep everything else.
