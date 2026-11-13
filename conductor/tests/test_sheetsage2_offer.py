"""The SheetSage2 offer follows the paired chord gain recorded in the claims file."""

import json
from pathlib import Path

import pytest
from ghostband.importing import ROOT

from scripts.measure_sheetsage2_gain import (
    offer_enabled,
    offer_line,
    offer_present,
    paired_gain,
    read_scores,
    render_table,
    write_claims,
)

ADR = ROOT / "docs" / "adr" / "0004-permissive-default-sheetsage2-offer.md"
CLAIMS = ROOT / "docs" / "benchmarks" / "claims.json"
HEADER = "group,track,duration_s,extractor,metric,value,failed\n"


def test_sheetsage2_offer_scores_ignore_other_groups_metrics_and_failures(tmp_path: Path) -> None:
    path = tmp_path / "extraction_tracks.csv"
    path.write_text(
        HEADER
        + "aam,a,30.000,sheetsage2,majmin,0.8,false\n"
        + "aam,a,30.000,permissive,majmin,0.5,false\n"
        + "aam,a,30.000,permissive,beat_f,0.9,false\n"
        + "aam,b,30.000,sheetsage2,majmin,,true\n"
        + "aam,b,30.000,permissive,majmin,0.4,false\n"
        + "aam,c,30.000,sheetsage2,majmin,,false\n"
        + "guitarset-comp,g,10.000,sheetsage2,majmin,0.1,false\n",
        encoding="utf-8",
    )
    assert read_scores(path) == {"sheetsage2": {"a": 0.8}, "permissive": {"a": 0.5, "b": 0.4}}


def test_sheetsage2_offer_needs_the_whole_interval_above_zero() -> None:
    better = {
        "sheetsage2": {"a": 0.8, "b": 0.6, "c": 0.9},
        "permissive": {"a": 0.5, "b": 0.5, "c": 0.6},
    }
    gain = paired_gain(better)
    assert gain is not None
    mean, low, high = gain
    # Every per-track difference lies in 0.1 to 0.3, so no resample mean can leave that range.
    assert mean == pytest.approx(0.7 / 3)
    assert 0.1 - 1e-9 <= low <= mean <= high <= 0.3 + 1e-9
    assert offer_enabled(gain)

    mixed = {
        "sheetsage2": {"a": 0.6, "b": 0.4, "c": 0.5},
        "permissive": {"a": 0.5, "b": 0.5, "c": 0.5},
    }
    undecided = paired_gain(mixed)
    assert undecided is not None
    assert undecided[0] == pytest.approx(0.0, abs=1e-12)
    assert undecided[1] < 0 < undecided[2]
    assert not offer_enabled(undecided)


def test_sheetsage2_offer_pairs_only_tracks_both_extractors_scored() -> None:
    scores = {"sheetsage2": {"a": 0.9, "x": 0.0}, "permissive": {"a": 0.4, "y": 1.0}}
    assert paired_gain(scores) == pytest.approx((0.5, 0.5, 0.5))
    assert paired_gain({"sheetsage2": {"x": 0.9}, "permissive": {"y": 0.4}}) is None
    assert not offer_enabled(None)
    assert not offer_enabled((0.0, 0.0, 0.1))


def test_sheetsage2_offer_line_quotes_the_difference_only_when_enabled() -> None:
    assert offer_line((0.25, 0.125, 0.375), 4) == (
        "Offer: enabled. On AAM chord majmin SheetSage2 beats the permissive extractor by "
        "0.250 per track (95% interval 0.125 to 0.375, 4 paired tracks)."
    )
    assert offer_line((0.25, -0.125, 0.5), 4) == (
        "Offer: not enabled. The per-track difference in AAM chord majmin is 0.250 "
        "(95% interval -0.125 to 0.500, 4 paired tracks), which does not sit above zero."
    )
    assert offer_line(None, 0) == (
        "Offer: not enabled. No track has a chord score from both extractors."
    )


def test_sheetsage2_offer_is_present_only_on_an_enabled_verdict_line() -> None:
    assert offer_present("# Title\n\nOffer: enabled. Quoted difference.\n")
    assert not offer_present("Offer: not enabled. Not enabled.\n")
    assert not offer_present("The offer: enabled in prose.\n")


def test_sheetsage2_offer_table_has_a_row_per_extractor_and_the_difference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "extraction_tracks.csv"
    path.write_text(
        HEADER
        + "aam,a,30.000,sheetsage2,majmin,0.75,false\n"
        + "aam,a,30.000,permissive,majmin,0.5,false\n",
        encoding="utf-8",
    )
    assert render_table(read_scores(path)) == (
        "| Extractor | Paired tracks | AAM chord majmin (95% interval) |\n"
        "| --- | ---: | --- |\n"
        "| permissive | 1 | 0.500 (0.500 to 0.500) |\n"
        "| sheetsage2 | 1 | 0.750 (0.750 to 0.750) |\n"
        "| sheetsage2 minus permissive | 1 | 0.250 (0.250 to 0.250) |\n"
    )


def test_sheetsage2_offer_claim_keeps_other_entries_and_writes_null_when_unmeasured(
    tmp_path: Path,
) -> None:
    path = tmp_path / "docs" / "claims.json"
    write_claims(path, None, 0)
    assert json.loads(path.read_text(encoding="utf-8")) == {"sheetsage2_gain": None}
    path.write_text(json.dumps({"melody_survives": True}), encoding="utf-8")
    write_claims(path, (0.25, 0.125, 0.375), 4)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "melody_survives": True,
        "sheetsage2_gain": {"mean": 0.25, "low": 0.125, "high": 0.375, "pairs": 4},
    }


def test_sheetsage2_offer_in_the_adr_follows_the_claims_file() -> None:
    text = ADR.read_text(encoding="utf-8")
    gain = None
    if CLAIMS.is_file():
        gain = json.loads(CLAIMS.read_text(encoding="utf-8")).get("sheetsage2_gain")
    assert offer_present(text) == (gain is not None and gain["low"] > 0)
    if gain is not None and offer_present(text):
        assert f"{gain['mean']:.3f}" in text
        assert f"{gain['low']:.3f} to {gain['high']:.3f}" in text
