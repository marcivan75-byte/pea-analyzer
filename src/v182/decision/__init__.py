__all__ = ["enrich_analyst_momentum", "consensus_score_100"]


def __getattr__(name):
    if name in __all__:
        from .analyst_momentum import enrich_analyst_momentum, consensus_score_100

        values = {
            "enrich_analyst_momentum": enrich_analyst_momentum,
            "consensus_score_100": consensus_score_100,
        }
        return values[name]
    raise AttributeError(name)