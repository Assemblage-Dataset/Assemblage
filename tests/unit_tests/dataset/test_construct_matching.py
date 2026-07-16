"""Unit tests for the db_construct Binary_info_list name-matching fix (R5).

The daily pipeline used to store zero DWARF functions because the DWARF entry's
``file`` (the on-disk download name, ``{binary_id}_{name}``) never equalled the
cleaned staged name db_construct compares against. ``staged_name_matches``
closes that gap; these tests pin its behaviour across the shapes ``file`` takes:
a bare name, the ``{binary_id}_`` re-extraction prefix, POSIX/Windows paths, and
the builder's already-clean Rust names.
"""

import pytest

pytest.importorskip("tqdm")
pytest.importorskip("pefile")

from assemblage.dataset.construct import staged_name_matches


def test_binary_id_prefixed_name_matches_clean_staged_name():
    # The pre-existing defect case: re-extraction names the entry after the raw
    # download file, db_construct strips down to "hello".
    assert staged_name_matches("1005_hello", "hello")


def test_bare_clean_name_matches():
    # Rust: the builder writes the clean basename into `file`.
    assert staged_name_matches("golden_bin", "golden_bin")


def test_posix_path_basename_is_used():
    assert staged_name_matches("target/release/1042_golden_bin", "golden_bin")
    assert staged_name_matches("/abs/path/1042_golden_bin", "golden_bin")


def test_windows_path_basename_is_used():
    assert staged_name_matches(r"C:\\build\\out\\2001_app.exe", "app.exe")
    assert staged_name_matches(r"out\\2001_app.exe", "app.exe")


def test_no_false_positive_on_different_binary():
    assert not staged_name_matches("1005_hello", "world")
    assert not staged_name_matches("1005_helloworld", "hello")


def test_only_leading_digit_prefix_is_stripped():
    # A name that legitimately contains an underscore but no numeric prefix must
    # match raw, and a numeric-looking infix must not be stripped.
    assert staged_name_matches("my_tool", "my_tool")
    assert staged_name_matches("42_my_tool", "my_tool")


def test_empty_or_missing_file_never_matches():
    assert not staged_name_matches("", "hello")
    assert not staged_name_matches(None, "hello")
