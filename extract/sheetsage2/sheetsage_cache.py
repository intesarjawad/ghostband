"""Fetch and verify the exact files required by offline transcription."""

import hashlib
from importlib import import_module
from pathlib import Path

from sheetsage_pins import MODELS, ModelFiles


def snapshot_path(cache_dir: Path, model: ModelFiles) -> Path:
    return (
        cache_dir
        / ("models--" + model.repository.replace("/", "--"))
        / "snapshots"
        / model.revision
    )


def verify_file(path: Path, size_bytes: int, sha256: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing cached file {path}; run the fetch command first")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != size_bytes or digest != sha256:
        raise ValueError(f"Checksum or size mismatch: {path}; remove it and fetch again")


def verify_cache(cache_dir: Path) -> None:
    """Check code, configuration, licences and weights before executing cached code."""
    for model in MODELS:
        for filename, size_bytes, digest in model.files:
            verify_file(snapshot_path(cache_dir, model) / filename, size_bytes, digest)


def fetch(cache_dir: Path) -> None:
    """Download pinned CC-BY-NC-4.0 weights for non-commercial use only."""
    hub = import_module("huggingface_hub")
    for model in MODELS:
        for filename, size_bytes, digest in model.files:
            downloaded: str = hub.hf_hub_download(
                repo_id=model.repository,
                filename=filename,
                revision=model.revision,
                cache_dir=str(cache_dir),
                token=False,
            )
            verify_file(Path(downloaded), size_bytes, digest)
    verify_cache(cache_dir)
