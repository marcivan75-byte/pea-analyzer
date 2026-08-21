from __future__ import annotations

from v182.sources import tct_catalyst_news as base


VERSION = "TCT_V24.4.1_CATALYST_NEWS"

_FRAUD_INVESTIGATION_PATTERNS = (
    "fraud investigation",
    "investigation into fraud",
    "accounting investigation",
    "criminal investigation",
    "regulatory investigation",
    "regulatory probe",
    "probe into fraud",
    "enquête pour fraude",
    "enquete pour fraude",
    "enquête réglementaire",
    "enquete reglementaire",
    "enquête pénale",
    "enquete penale",
    "ermittlungen wegen betrugs",
)


def classify_headline(headline: str, event_weights: dict) -> tuple[str, float, float]:
    """Classify catalysts conservatively.

    V24.4.0 treated generic words such as ``investigation`` as a severe
    FRAUD_INVESTIGATION. V24.4.1 requires an explicit fraud, accounting,
    criminal or regulatory context before applying the high-severity event.
    Other event families keep the frozen V24.4 pattern catalogue.
    """
    text = base._normalise_headline(headline)
    if any(pattern in text for pattern in _FRAUD_INVESTIGATION_PATTERNS):
        spec = event_weights.get("FRAUD_INVESTIGATION", {})
        return (
            "FRAUD_INVESTIGATION",
            float(spec.get("magnitude", 95.0)),
            float(spec.get("direction", -90.0)),
        )

    for event_type, patterns in base._EVENT_PATTERNS:
        if event_type == "FRAUD_INVESTIGATION":
            continue
        if any(pattern in text for pattern in patterns):
            spec = event_weights.get(event_type, {})
            return event_type, float(spec.get("magnitude", 50.0)), float(spec.get("direction", 0.0))

    spec = event_weights.get("OTHER_NEWS", {})
    return "OTHER_NEWS", float(spec.get("magnitude", 35.0)), float(spec.get("direction", 0.0))


# The base scorer resolves ``classify_headline`` from its own module globals.
# Patch only that primitive, then reuse the already-audited PIT windowing,
# timestamp filtering, deduplication, corroboration and bounded fetch logic.
base.classify_headline = classify_headline

CatalystNews = base.CatalystNews
parse_article_timestamp = base.parse_article_timestamp
filter_articles_to_window = base.filter_articles_to_window
score_windowed_articles = base.score_windowed_articles
build_company_query = base.build_company_query
fetch_candidate_news = base.fetch_candidate_news
