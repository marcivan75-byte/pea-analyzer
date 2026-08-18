from v182.sources.gdelt_news import NewsScore, score_queries


def test_gdelt_batch_deduplicates_exact_queries(monkeypatch):
    import v182.sources.gdelt_news as gdelt

    calls=[]

    def fake_uncached(query,timespan,max_records,limiter=None):
        calls.append((query,timespan,max_records))
        return NewsScore(60.0,2,2,0,"GDELT"),None

    monkeypatch.setattr(gdelt,"_score_query_uncached",fake_uncached)
    result=score_queries(
        ["same query","same query","other query"],
        timespan="2d",max_records=50,delay_seconds=0,max_workers=2,
    )
    assert set(result)=={"same query","other query"}
    assert len(calls)==2
    assert all(timespan=="2d" and max_records==50 for _,timespan,max_records in calls)
