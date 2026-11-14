"""Review 6, items 2 and 4: the appeal-rule changes (Humana split, UnitedHealthcare split from UMR/Optum/Surest, rules
marked confirmed) and DETECT_VERSION. Invented data only; the payer group names are the ones codes.normalize_payer and
codes.payer_group_kind produce."""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import types

import pytest

from tests.review6.r6helpers import REPO
from remitwatch.codes import appeal_rule  # noqa: E402

GROUPS = [
    ("Anthem / BCBS", "commercial"), ("Anthem / BCBS Medicare Advantage", "medicare_advantage"), ("Anthem Medicare Advantage", "medicare_advantage"),
    ("Anthem / BCBS Medicaid", "medicaid"), ("Medicare KY (CGS J15)", "medicare"), ("Medicare IN (WPS J8)", "medicare"), ("Railroad Medicare", "medicare"),
    ("Medicare (other)", "medicare"), ("Humana Medicare Advantage", "medicare_advantage"), ("Humana", "commercial"), ("Humana Medicaid", "medicaid"),
    ("Humana Medicare Advantage Medicaid", "medicaid"), ("UnitedHealthcare", "commercial"), ("UnitedHealthcare Medicare Advantage", "medicare_advantage"),
    ("UHC Medicare Advantage", "medicare_advantage"), ("UHC Integrated Services (Wausau)", "commercial"), ("UMR", "commercial"),
    ("Optum (UHC)", "commercial"), ("Surest (UHC)", "commercial"), ("AARP Medicare Supplement (UHC)", "supplement"), ("Aetna", "commercial"),
    ("Cigna", "commercial"), ("Medicare Supplement carriers", "supplement"), ("Tricare", "tricare"), ("Self-funded plan TPAs", "commercial"),
    ("WellCare / Ambetter (Centene) Medicaid", "medicaid"), ("UMWA Health & Retirement Funds", "commercial"),
]


def live_codes():
    """codes.py as the live release (6dcc450) has it, loaded on its own."""
    try:
        where = os.environ.get("REVIEW_GIT_DIR", REPO)        # a checkout made with git archive has no history: point at the repository
        src = subprocess.run(["git", "-C", where, "show", "6dcc450:remitwatch/codes.py"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("the live release is not in this checkout")
    mod = types.ModuleType("rw_live_codes")
    exec(compile(src, "live_codes.py", "exec"), mod.__dict__)
    return mod


def test_no_payer_that_matched_before_gets_a_different_appeal_window():
    """Fine: for every payer group the archive has, the number of days is what the live release used."""
    live = live_codes()
    for grp, kind in GROUPS:
        for plan in (None, "FEDERAL EMPLOYEE PROGRAM"):
            assert appeal_rule(grp, kind, plan)["days"] == live.appeal_rule(grp, kind, plan)["days"], (grp, kind, plan)


def test_a_unitedhealthcare_administered_tpa_is_now_called_confirmed():
    """The split keeps UMR, Optum and Surest unconfirmed ('the plan's own rules not checked'), but 'UHC Integrated
    Services (Wausau)', the UnitedHealth-administered self-funded plans that remittances name 'UNITEDHEALTH INTEGRATED
    SERVICE - FORMERLY WAUSAU', starts with 'UHC' and so takes the confirmed UnitedHealthcare label. The live release
    called it unconfirmed."""
    live = live_codes()
    assert live.appeal_rule("UHC Integrated Services (Wausau)", "commercial")["verified"] is False
    assert appeal_rule("UMR", "commercial")["verified"] is False
    wausau = appeal_rule("UHC Integrated Services (Wausau)", "commercial")
    assert wausau["verified"] is False, f"confirmed as: {wausau['label']}"


# --------------------------------------------------------------------------- DETECT_VERSION

def _version_of(tree: str, alias: str) -> str:
    pkg = os.path.join(tree, "remitwatch")
    spec = importlib.util.spec_from_file_location(alias, os.path.join(pkg, "__init__.py"), submodule_search_locations=[pkg])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return importlib.import_module(alias + ".detect").DETECT_VERSION


def test_rules_version_follows_the_files_whose_rules_detection_applies(tmp_path):
    """Fine: a change to codes.py, fees.py or link.py changes DETECT_VERSION (so the next cycle re-analyses once, which
    tests/test_features.py and tests/test_cloud.py already prove for both hosts); a change to a page module does not."""
    base = tmp_path / "base"
    shutil.copytree(os.path.join(REPO, "remitwatch"), base / "remitwatch", ignore=shutil.ignore_patterns("__pycache__", "static"))
    v0 = _version_of(str(base), "rw_v0")
    seen = {}
    for i, name in enumerate(("codes.py", "fees.py", "link.py", "api.py")):
        tree = tmp_path / f"t{i}"
        shutil.copytree(base, tree)
        with open(tree / "remitwatch" / name, "a") as fh:
            fh.write("\n# changed\n")
        seen[name] = _version_of(str(tree), f"rw_v{i + 1}")
    assert seen["codes.py"] != v0 and seen["fees.py"] != v0 and seen["link.py"] != v0
    assert seen["api.py"] == v0
