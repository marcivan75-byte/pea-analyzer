from __future__ import annotations

from v182.decision.actions_v210_prepare import build as prepare_v21
from v182.decision.actions_v210_enrichment import apply as enrich_v21
from v182.decision.actions_v210_committee import build as committee_v21
from v182.decision.tct_explosif_enrichment_v211 import apply as enrich_tct
from v182.decision.tct_explosif_v211 import build as score_tct


def main() -> None:
    prepare_v21()
    enrich_v21()
    committee_v21()
    enrich_tct()
    score_tct()


if __name__ == "__main__":
    main()
