# insta_save/enrich_schema.py
"""results.json contract for the one-shot enrich + pure tag validation.

Each result item the backend produces:
    {page_id, source_id, title, summary, externals, content_type, topics[]}

The claude-code backend is not constrained-decoded (unlike Ollama's format=),
so tags are validated here: keep content_type only if in vocab (else blank it —
a review escape hatch; never guess a replacement), and dedupe / drop-out-of-vocab
/ clamp topics to MAX_TOPICS. Pure, no I/O."""

RESULT_FIELDS = ("page_id", "source_id", "title", "summary", "externals",
                 "content_type", "topics")

MAX_TOPICS = 3


def validate_item(item, allowed_content_types, allowed_topics):
    """Return a cleaned (content_type, topics) pair for one result item.

    content_type -> kept iff in allowed_content_types, else None.
    topics       -> order-preserving dedupe, out-of-vocab dropped, clamped to MAX_TOPICS.
    """
    ct = item.get("content_type")
    content_type = ct if ct in allowed_content_types else None

    kept: list[str] = []
    for topic in item.get("topics") or []:
        if topic in allowed_topics and topic not in kept:
            kept.append(topic)
    return content_type, kept[:MAX_TOPICS]


def tags_for(content_type, topics):
    """Compose the Notion multi_select list: content_type first, then topics.
    A blanked (None) content_type contributes nothing."""
    return ([content_type] if content_type else []) + list(topics)
