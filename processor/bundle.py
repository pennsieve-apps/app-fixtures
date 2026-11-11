"""A fixture bundle: the output directory of an earlier build of these
fixtures, as the App Store's release pipeline stores it (app-deploy-service
docs/builds.md "Fixture bundles"), unpacked instead of built. BUNDLE_URL is
a presigned (or file://) URL of the .tar.gz; the layout inside is exactly
OUTPUT_DIR, including .pennsieve-fixtures/ with the provenance crate or the
error.json of a failed build, which is reported as if it happened here."""
from __future__ import annotations

import hashlib
import logging
import tarfile
import urllib.request
from pathlib import Path

from .errors import BUNDLE_INVALID, BUNDLE_TOO_LARGE, BUNDLE_UNAVAILABLE, FixtureError

log = logging.getLogger("fixtures")

DOWNLOAD_TIMEOUT = 60


def download(url: str, dest: Path, max_bytes: int) -> tuple[int, str]:
    """Fetch the bundle to dest; its size and sha256. Stops at max_bytes."""
    if not url.startswith(("https://", "http://", "file://")):
        raise FixtureError("BUNDLE_URL must be an https:// or file:// URL", BUNDLE_INVALID)
    h = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as r, open(dest, "wb") as w:  # noqa: S310 - the URL scheme is checked above
            while chunk := r.read(1 << 20):
                size += len(chunk)
                if size > max_bytes:
                    raise FixtureError(f"the bundle is larger than {max_bytes} bytes", BUNDLE_TOO_LARGE)
                h.update(chunk)
                w.write(chunk)
    except FixtureError:
        raise
    except Exception as e:  # URLError, HTTPError, OSError, timeout
        raise FixtureError(f"could not download the bundle: {e}", BUNDLE_UNAVAILABLE) from e
    return size, h.hexdigest()


def _safe_member(member: tarfile.TarInfo, root: Path) -> bool:
    """Only regular files and directories, inside root."""
    if not (member.isfile() or member.isdir()):
        return False
    target = (root / member.name).resolve()
    return target == root or root in target.parents


def unpack(url: str, output: Path, max_bytes: int) -> dict:
    """Download and extract the bundle into output. Returns what was done
    (bytes, sha256, files) for the log."""
    output.mkdir(parents=True, exist_ok=True)
    archive = output.parent / ".fixtures-bundle.tar.gz"
    log.info("bundle: downloading %s", url.split("?", 1)[0])
    size, digest = download(url, archive, max_bytes)
    files = 0
    root = output.resolve()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                if not _safe_member(member, root):
                    log.warning("bundle: skipping %s", member.name)
                    continue
                # Python 3.12's data filter refuses unsafe members too; the
                # check above is what matters on older interpreters.
                extract_args = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
                tar.extract(member, path=root, set_attrs=False, **extract_args)
                if member.isfile():
                    files += 1
    except (tarfile.TarError, OSError, ValueError) as e:
        raise FixtureError(f"the bundle is not a readable tar.gz: {e}", BUNDLE_INVALID) from e
    finally:
        archive.unlink(missing_ok=True)
    log.info("bundle: unpacked %d file(s) from %d bytes into %s", files, size, output)
    return {"bytes": size, "sha256": digest, "files": files}
