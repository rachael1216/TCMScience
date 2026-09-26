"""Data acquisition — declarative sources, a verified downloader, and auto-fetch.

A dataset component that is *declared* but not *present* used to resolve to
UNAVAILABLE with nothing a user could do about it. Now a manifest may carry an
acquisition spec (URL, expected size, checksum, license, strategy); the resolver
reports such a component as FETCHABLE with the exact command to obtain it; and
the downloader fetches it with resumable ranged requests, checksum verification,
and an atomic rename so a partial file is never mistaken for data.
"""

from .downloader import DownloadError, DownloadResult, Downloader
from .sources import (ACQUIRABLE, AcquisitionSpec, BiomniLakeSource, BulkDatasetProvider,
                      acquisition_for, fetch_command)

__all__ = ["Downloader", "DownloadResult", "DownloadError", "AcquisitionSpec", "ACQUIRABLE",
           "BulkDatasetProvider", "BiomniLakeSource", "acquisition_for", "fetch_command"]
