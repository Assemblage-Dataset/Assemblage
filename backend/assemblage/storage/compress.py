"""zstd compression for stored artifacts.

Everything the builder puts in the artifacts bucket is stored compressed. The
corpus is overwhelmingly compressible and was not exploiting it: measured over
2,000 published builds, zstd level 12 gives **5.70x on ELF binaries** and
**37.64x on the metadata JSON** (DWARF function/line records), 6.2x across the
bucket as a whole — 2.3 TiB down to ~370 GiB.

Level 12 is not tuned here, it is *inherited*: it is what the published
HuggingFace corpus already uses, and matching it is the point. A build exported
for release is then a byte copy rather than a decompress-recompress cycle.

Streaming throughout — a 250 MB Rust binary must never be held in memory twice
on a builder that is already running cargo under a memory cap.
"""

import io
import os
from pathlib import Path

import zstandard

#: Matches the published corpus. Changing it re-costs every future export.
COMPRESS_LEVEL = 12

#: Read/write chunk for streaming. Large enough that syscall overhead is noise
#: on a 250 MB binary, small enough to stay invisible next to a cargo build.
_CHUNK = 4 << 20


def compress_file(src: str | Path, dst: str | Path, level: int = COMPRESS_LEVEL) -> int:
    """Compress ``src`` to ``dst``; return the stored (compressed) byte count."""
    compressor = zstandard.ZstdCompressor(level=level)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        compressor.copy_stream(fin, fout, read_size=_CHUNK, write_size=_CHUNK)
    return os.path.getsize(dst)


def decompress_file(src: str | Path, dst: str | Path) -> int:
    """Decompress ``src`` to ``dst``; return the restored byte count."""
    decompressor = zstandard.ZstdDecompressor()
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        decompressor.copy_stream(fin, fout, read_size=_CHUNK, write_size=_CHUNK)
    return os.path.getsize(dst)


def compress_bytes(blob: bytes, level: int = COMPRESS_LEVEL) -> bytes:
    """Compress an in-memory blob (used for the small JSON payloads)."""
    return zstandard.ZstdCompressor(level=level).compress(blob)


def decompress_bytes(blob: bytes) -> bytes:
    """Decompress an in-memory blob.

    Uses ``stream_reader`` rather than ``decompress`` because the latter requires
    the frame to declare its content size, which streamed frames do not.
    """
    decompressor = zstandard.ZstdDecompressor()
    with decompressor.stream_reader(io.BytesIO(blob)) as reader:
        return reader.read()
