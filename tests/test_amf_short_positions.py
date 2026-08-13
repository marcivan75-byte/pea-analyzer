from __future__ import annotations

from datetime import date

from v182.sources.amf_short_positions import fetch_current_public_shorts


class FakeResponse:
    status_code=200
    ok=True
    content=(
        "Nom du détenteur;LEI du détenteur;Nom de l'émetteur;ISIN;Position courte nette;Date de début de position;Date de début de publication;Date de fin de publication\n"
        "Holder A;LEI-A;TotalEnergies;FR0000120271;0,60;01/08/2026;02/08/2026;\n"
        "Holder B;LEI-B;TotalEnergies;FR0000120271;0,45;03/08/2026;04/08/2026;\n"
        "Holder C;LEI-C;BNP Paribas;FR0000131104;0,70;01/07/2026;02/07/2026;05/08/2026\n"
    ).encode("utf-8")

    def raise_for_status(self):
        return None


class FakeRequests:
    calls=[]

    @classmethod
    def get(cls,url,timeout=None,allow_redirects=None,headers=None):
        cls.calls.append((url,timeout,allow_redirects,headers))
        return FakeResponse()


def test_amf_aggregates_only_current_published_positions_without_zero_imputation():
    FakeRequests.calls=[]
    rows,failures,stats=fetch_current_public_shorts(as_of=date(2026,8,13),requests_module=FakeRequests)
    assert failures == []
    assert len(rows) == 1
    row=rows[0]
    assert row["isin"] == "FR0000120271"
    assert row["public_short_pct"] == 1.05
    assert row["amf_public_short_ge_0_5_pct"] == 0.6
    assert row["amf_public_short_holders_count"] == 2
    assert row["amf_public_short_max_holder_pct"] == 0.6
    assert row["amf_public_short_below_0_5_warning"] is True
    assert stats["no_zero_imputation"] is True
    assert stats["active_isins"] == 1


def test_amf_schema_mismatch_fails_explicitly():
    class BadResponse(FakeResponse):
        content=b"foo;bar\n1;2\n"

    class BadRequests:
        @classmethod
        def get(cls,*args,**kwargs):
            return BadResponse()

    rows,failures,stats=fetch_current_public_shorts(as_of=date(2026,8,13),requests_module=BadRequests)
    assert rows == []
    assert failures[0]["reason"] == "SCHEMA_MISMATCH"
    assert stats["status"] == "SCHEMA_MISMATCH"
