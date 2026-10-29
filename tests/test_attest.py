"""The attestation (``fiduciary.attest``) and its reports (``fiduciary.report``),
and ``verify-bundle --json`` and ``--report``: over every golden bundle, the
exit code is the one recorded, the JSON says the same verdict as the text,
and a report is written without changing either."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from fiduciary import cli, report
from fiduciary.attest import CONTROLS, FRAMEWORKS, PROVES, SCHEMA, attest
from fiduciary.bundle import CHECKS, TrustedKeys, verify_bundle

BUNDLES = Path(__file__).resolve().parents[1] / "vectors" / "fidu1" / "bundles"
INDEX: list[dict[str, Any]] = json.loads((BUNDLES / "index.json").read_text())
PASSING = next(c for c in INDEX if c["exit_code"] == 0)


def _argv(case: dict[str, Any], keys: Path) -> list[str]:
    argv = ["verify-bundle", str(BUNDLES / case["file"]), "--charter", case["charter"]]
    if case["keys"] is not None:
        keys.write_text(json.dumps(case["keys"]))
        argv += ["--keys", str(keys)]
    if case["witness"]:
        argv += ["--witness", case["witness"]]
    return [*argv, "--skew", str(case["skew_seconds"])]


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        status = cli.main(argv)
    return status, out.getvalue()


@pytest.mark.parametrize("case", INDEX, ids=[c["name"] for c in INDEX])
def test_json_keeps_every_golden_exit_code(case: dict[str, Any], tmp_path: Path) -> None:
    argv = _argv(case, tmp_path / "keys.json")
    status, _ = _run([*argv, "--json"])
    assert status == case["exit_code"]
    if case["exit_code"] in (1, 2):
        return  # never verified: no attestation
    status, printed = _run([*argv, "--json"])
    attested = json.loads(printed)
    assert attested["schema"] == SCHEMA
    assert attested["verdict"]["exit_code"] == case["exit_code"]
    assert attested["verdict"]["passed"] is (case["exit_code"] == 0)
    failing = sorted(c["id"] for c in attested["checks"] if c["status"] == "fail")
    assert failing == sorted(case["checks"])
    found = [
        f"{c['id']} {f['kind']}{f' [{x}]' if (x := f['label']) else ''}: {f['message']}"
        for c in attested["checks"]
        for f in c["findings"]
    ]
    assert sorted(found) == sorted(case["findings"])


@pytest.mark.parametrize("suffix", [".html", ".md"])
def test_a_report_is_written_beside_the_verdict(suffix: str, tmp_path: Path) -> None:
    case = next(c for c in INDEX if c["name"] == "v7-break-glass-release")
    argv = _argv(case, tmp_path / "keys.json")
    plain = _run(argv)
    target = tmp_path / f"report{suffix}"
    assert _run([*argv, "--report", str(target)]) == plain
    text = target.read_text()
    assert "BREAK-GLASS DETECTED" in text
    if suffix == ".md":
        assert "| V7 completeness | **FAIL** |" in text
        assert "[break-glass]" in text


def test_a_report_must_be_html_or_markdown(tmp_path: Path) -> None:
    case = PASSING
    with contextlib.redirect_stderr(io.StringIO()) as err:
        status = cli.main([*_argv(case, tmp_path / "k.json"), "--report", str(tmp_path / "r.pdf")])
    assert status == 1
    assert "--report writes a .html or a .md file" in err.getvalue()
    with contextlib.redirect_stderr(io.StringIO()):
        status = cli.main(
            [*_argv(case, tmp_path / "k.json"), "--report", str(tmp_path / "none" / "r.md")]
        )
    assert status == 1


def test_every_check_is_mapped_to_frameworks_it_names() -> None:
    assert sorted(PROVES) == sorted(CONTROLS) == sorted(check for check, _ in CHECKS)
    for check, mapping in CONTROLS.items():
        for framework, controls in mapping.items():
            assert set(controls) <= set(FRAMEWORKS[framework]), (check, framework)
    assert list(FRAMEWORKS["owasp-asi-2026"]) == [f"ASI{n:02d}" for n in range(1, 11)]


def test_the_attestation_reads_what_the_bundle_holds() -> None:
    case = next(c for c in INDEX if c["name"] == "v7-break-glass-release")
    data = (BUNDLES / case["file"]).read_bytes()
    keys = TrustedKeys(case["keys"]) if case["keys"] else None
    found = verify_bundle(data, charter=case["charter"], keys=keys)
    attested = attest(data, found, charter=case["charter"], pinned=sorted(case["keys"] or {}))
    assert attested["verdict"]["status"] == "BREAK-GLASS DETECTED"
    assert attested["verdict"]["first_failing"] == "V7"
    assert attested["bundle"]["members"] == sum(attested["bundle"]["member_types"].values()) + 1
    (ambient,) = [o for o in attested["operators"] if o["ambient"]]
    assert ambient["label"] == "break-glass" and ambient["action"] == "release"
    assert attested["authority"]["charter"]["break_glass"]
    assert attested["tickets"] and attested["authority"]["mandates"]
    json.dumps(attested)  # it is JSON as it stands


def test_the_html_embeds_its_payload_safely() -> None:
    case = PASSING
    data = (BUNDLES / case["file"]).read_bytes()
    keys = TrustedKeys(case["keys"]) if case["keys"] else None
    found = verify_bundle(data, charter=case["charter"], keys=keys)
    attested = attest(data, found, charter=case["charter"])
    attested["notes"].append("</script><script>alert(1)</script> & more")
    page = report.html(report.payload({"bundle": attested}))
    assert page.count("</script>") == 2
    assert "{{TITLE}}" not in page and "/*DATA*/" not in page
    start = page.index('id="payload">') + len('id="payload">')
    embedded = json.loads(page[start : page.index("</script>", start)])
    assert embedded["attestations"]["bundle"]["notes"][-1].startswith("</script>")


def test_what_the_attestation_cannot_read_it_leaves_out() -> None:
    from fiduciary.attest import _ledger, _lines, _operators, _version

    assert _version("no-such-distribution") == "unknown"
    assert _lines(b'{"seq": 1}\nnot json\n{"seq": 2}\n') == [{"seq": 1}, {"seq": 2}]
    from fiduciary.bundle import BundleReport

    assert _operators([], {"operators": {"night": "not a key"}}, BundleReport()) == []
    entries = [
        {"entry": {"entry_type": "hold", "scope_id": "a", "amount": "1.5", "entry_id": "h1"}},
        {"entry": {"entry_type": "hold", "scope_id": "a", "amount": "2", "entry_id": "h2"}},
        {"entry": {"entry_type": "spend", "scope_id": "a", "amount": "2", "ref": "h2"}},
        {"entry": {"entry_type": "funding", "scope_id": "b", "amount": "x", "direction": "CR"}},
    ]
    ledger = _ledger(entries, ["a"])
    (row,) = ledger["scopes"]
    assert (row["scope"], row["held"], row["spent"]) == ("a", "1.50000000", "2.00000000")
    assert ledger["by_type"] == {"funding": 1, "hold": 2, "spend": 1}


# -- the control-framework packs -------------------------------------------------------

PACK_CONTROLS = {
    "hipaa": [
        "§164.312(a)(1)",
        "§164.312(b)",
        "§164.312(c)(1)",
        "§164.312(d)",
        "§164.308(a)(1)(ii)(D)",
        "§164.502(b)",
    ],
    "part11": ["§11.10(e)", "§11.10(f)", "§11.10(g)", "§11.50", "§11.70"],
    "iso27001": ["5.3", "5.15", "5.28", "8.2", "8.3", "8.9", "8.15", "8.32"],
    "nist800_53": ["AC-5", "AC-6", "AU-2", "AU-3", "AU-9", "AU-10", "CM-3", "CM-5", "SI-7"],
    "sox_itgc": ["ITGC-SOD", "ITGC-PCC", "ITGC-MDA"],
    "gdpr": ["Art. 5(1)(f)", "Art. 5(2)", "Art. 25", "Art. 32"],
}


def test_each_new_pack_names_exactly_its_controls_and_maps_every_one() -> None:
    for name, controls in PACK_CONTROLS.items():
        assert list(FRAMEWORKS[name]) == controls, name
        mapped = {c for m in CONTROLS.values() for c in m.get(name, ())}
        assert mapped == set(controls), (name, set(controls) - mapped)


def test_the_first_three_packs_are_the_mapping_1_1_shipped() -> None:
    """Moving SOC 2, OWASP and the EU AI Act into data files changed no mapping."""
    assert list(FRAMEWORKS["soc2"]) == ["CC6.1", "CC6.2", "CC6.3", "CC7.2", "CC7.3", "CC8.1"]
    assert list(FRAMEWORKS["eu-ai-act"]) == ["Art. 12", "Art. 14"]
    assert CONTROLS["V7"]["owasp-asi-2026"] == ("ASI03", "ASI10")
    assert CONTROLS["V8"]["soc2"] == ("CC6.2", "CC8.1")
    assert "owasp-asi-2026" not in CONTROLS["V10"]
    uncovered = set(FRAMEWORKS["owasp-asi-2026"]) - {
        c for m in CONTROLS.values() for c in m.get("owasp-asi-2026", ())
    }
    assert uncovered == {"ASI05"}  # outside what a bundle records, and the pack says so


def test_the_attestation_records_each_pack_by_digest_and_says_what_it_is_not() -> None:
    import hashlib

    from fiduciary import frameworks

    data = (BUNDLES / PASSING["file"]).read_bytes()
    keys = TrustedKeys(PASSING["keys"]) if PASSING["keys"] else None
    found = verify_bundle(data, charter=PASSING["charter"], keys=keys)
    attested = attest(data, found, charter=PASSING["charter"])
    mappings = attested["mappings"]
    assert "not a certification" in mappings["disclaimer"]
    assert [p["framework"] for p in mappings["packs"]] == list(frameworks.ORDER)
    for summary in mappings["packs"]:
        raw = json.loads(
            (Path(frameworks.__file__).parent / f"{summary['framework']}.json").read_text()
        )
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert summary["digest"] == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        assert summary["covered"] <= summary["controls"]
    assert len({p["digest"] for p in mappings["packs"]}) == len(mappings["packs"])


def test_a_mapping_never_moves_a_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reports never change verdicts: an empty mapping attests the same verdict."""
    import fiduciary.attest as module

    case = next(c for c in INDEX if c["name"] == "v7-break-glass-release")
    data = (BUNDLES / case["file"]).read_bytes()
    keys = TrustedKeys(case["keys"]) if case["keys"] else None
    found = verify_bundle(data, charter=case["charter"], keys=keys)
    before = attest(data, found, charter=case["charter"])
    monkeypatch.setattr(module, "CONTROLS", {check: {} for check, _ in CHECKS})
    after = attest(data, found, charter=case["charter"])
    assert after["verdict"] == before["verdict"]
    assert [c["findings"] for c in after["checks"]] == [c["findings"] for c in before["checks"]]


def test_a_malformed_pack_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from fiduciary import frameworks

    good = json.loads((Path(frameworks.__file__).parent / "sox_itgc.json").read_text())
    cases = [
        ({**good, "framework": "other"}, "names framework"),
        ({**good, "evidence": {"V99": ["ITGC-SOD"]}}, "not a check"),
        ({**good, "evidence": {"V1": ["ITGC-XYZ"]}}, "unknown controls"),
        ({**good, "limits": {"ITGC-XYZ": "x"}}, "limits name unknown"),
    ]
    for raw, said in cases:

        class Text:
            def read_text(self, encoding: str = "utf-8", _raw: Any = raw) -> str:
                return json.dumps(_raw)

        class Folder:
            def __truediv__(self, name: str) -> Text:
                return Text()

        monkeypatch.setattr(frameworks, "files", lambda _: Folder())
        with pytest.raises(ValueError, match=said):
            frameworks._read("sox_itgc")
    with pytest.raises(KeyError, match="no control-framework pack"):
        frameworks.load("pci-dss")
