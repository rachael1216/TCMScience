"""Bulk dataset specs: well-formed, hosts allowed, the natural-product tranche pinned.

These run offline. The live check — that every URL still answers with the declared size
or checksum — is what ``Downloader.fetch`` performs at acquisition time, so a stale entry
fails loudly then rather than silently here.
"""

from __future__ import annotations

import re
import urllib.parse

import pytest

from bioagent.acquisition import ACQUIRABLE, BulkDatasetProvider, acquisition_for
from bioagent.policy import PROFILES

pytestmark = pytest.mark.unit

NATURAL_PRODUCT_PROJECTS = {"NPASS", "CMAUP", "NPAtlas", "LOTUS"}


def test_every_spec_is_well_formed_and_unique():
    filenames = [s.filename for s in ACQUIRABLE]
    assert len(set(filenames)) == len(filenames), "filenames double as dataset ids"
    for spec in ACQUIRABLE:
        parsed = urllib.parse.urlsplit(spec.url)
        assert parsed.scheme == "https" and parsed.netloc == spec.host, spec.url
        assert spec.license and spec.description and spec.domain and spec.source_project
        if spec.checksum:
            assert re.fullmatch(r"(md5|sha256):[0-9a-f]{32,64}", spec.checksum), spec.checksum
        if spec.expected_bytes is not None:
            assert spec.expected_bytes > 0


def test_every_dataset_host_is_allowed_by_the_research_profile():
    profile = PROFILES["biomedical-research"]
    for spec in ACQUIRABLE:
        assert any(spec.host == a or spec.host.endswith("." + a) for a in profile.allowed_hosts), \
            f"{spec.host} ({spec.source_project}) is not in the biomedical-research allowlist"


def test_the_natural_product_tranche_is_pinned():
    tranche = [s for s in ACQUIRABLE if s.source_project in NATURAL_PRODUCT_PROJECTS]
    assert {s.source_project for s in tranche} == NATURAL_PRODUCT_PROJECTS
    assert len(tranche) == 14
    for spec in tranche:
        assert spec.domain == "natural-products"
        assert spec.expected_bytes, f"{spec.filename} has no pinned size"
    lotus = [s for s in tranche if s.source_project == "LOTUS"]
    assert all(s.checksum and s.checksum.startswith("md5:") for s in lotus)
    assert all(s.host == "bidd.group" for s in tranche if s.source_project in ("NPASS", "CMAUP"))


def test_the_taxonomy_dump_is_size_verified_at_fetch_time_not_pinned():
    tax = next(s for s in ACQUIRABLE if s.filename == "taxdmp.zip")
    assert tax.expected_bytes is None and "Content-Length" in tax.notes
    assert tax.license.startswith("Public domain")


def test_the_provider_yields_valid_fetchable_manifests():
    manifests = list(BulkDatasetProvider().discover())
    assert len(manifests) == len(ACQUIRABLE) == 24
    assert len({m.id for m in manifests}) == len(manifests)
    for m in manifests:
        assert m.validate() == []
        assert m.runtime.backend == "dataset"
        spec = acquisition_for(m)
        assert spec is not None and spec.url.startswith("https://")
        assert m.permissions.network == (spec.host,)
    ids = {m.id for m in manifests}
    assert "lotus.dataset.260413_frozen_csv_gz" in ids
    assert "npass.dataset.npassv2_0_download_naturalproducts_activities_txt" in ids
