"""Hashing utilities for file integrity verification."""

import hashlib
from pathlib import Path


def compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hash of data.

    Args:
        data: Bytes to hash.

    Returns:
        Hash string prefixed with "sha256:".
    """
    hash_obj = hashlib.sha256(data)
    return f"sha256:{hash_obj.hexdigest()}"


def compute_file_hash(path: Path, chunk_size: int = 8192) -> str:
    """Compute SHA-256 hash of file in chunks for memory efficiency.

    Args:
        path: Path to file.
        chunk_size: Size of chunks to read.

    Returns:
        Hash string prefixed with "sha256:".

    Raises:
        FileNotFoundError: If file doesn't exist.
        IOError: If file can't be read.
    """
    hash_obj = hashlib.sha256()

    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hash_obj.update(chunk)

    return f"sha256:{hash_obj.hexdigest()}"


def verify_hash(data: bytes, expected_hash: str) -> bool:
    """Verify data matches expected hash.

    Args:
        data: Bytes to verify.
        expected_hash: Expected hash string (with "sha256:" prefix).

    Returns:
        True if hash matches.
    """
    computed = compute_sha256(data)
    return computed == expected_hash


def verify_file_hash(path: Path, expected_hash: str, chunk_size: int = 8192) -> bool:
    """Verify file matches expected hash.

    Args:
        path: Path to file.
        expected_hash: Expected hash string.
        chunk_size: Size of chunks to read.

    Returns:
        True if hash matches.
    """
    try:
        computed = compute_file_hash(path, chunk_size)
        return computed == expected_hash
    except (FileNotFoundError, IOError):
        return False


def short_hash(data: bytes, length: int = 8) -> str:
    """Compute shortened hash for display purposes.

    Args:
        data: Bytes to hash.
        length: Number of hex characters to return.

    Returns:
        Shortened hash string.
    """
    full_hash = hashlib.sha256(data).hexdigest()
    return full_hash[:length]
