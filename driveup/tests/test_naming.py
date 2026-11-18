"""Naming engine — pure-function tests (milestone 5)."""
from __future__ import annotations

import datetime as dt

import pytest

from driveup.core.naming import (ILLEGAL, NamingOptions, apply_transform, build_name,
                                 byte_len, dedupe_names, ext_from_mime, fit_name,
                                 human_size, infer_ext, nat_key, natural_sorted,
                                 preview_rows, render_template, sanitize, sniff_ext,
                                 split_ext, strip_emoji, truncate_bytes)


def test_template_with_padded_sequence():
    opt = NamingOptions(template="{seq:03d} - {original}.{ext}")
    assert build_name(original="Intro.mp4", mime="video/mp4", seq=1, index=1, total=1,
                      opt=opt) == "001 - Intro.mp4"


def test_extension_added_when_template_omits_it():
    opt = NamingOptions(template="Module 1 - {index:02d} {original}")
    assert build_name(original="Data types.mkv", mime="video/x-matroska", seq=2, index=2,
                      total=9, opt=opt) == "Module 1 - 02 Data types.mkv"


def test_extension_not_doubled_when_template_includes_it():
    opt = NamingOptions(template="{original}.{ext}")
    assert build_name(original="a.mp4", mime="video/mp4", seq=1, index=1, total=1,
                      opt=opt) == "a.mp4"


def test_date_time_size_type_variables():
    opt = NamingOptions(template="{date}_{time}_{size}_{type}")
    when = dt.datetime(2026, 11, 15, 9, 5, 3)
    out = build_name(original="x.pdf", mime="application/pdf", seq=1, index=1, total=1,
                     opt=opt, size=1536, when=when)
    assert "2026-11-15" in out and "09-05-03" in out and "1.50 KB" in out and "document" in out


def test_unknown_placeholder_is_empty_never_raises():
    assert render_template("{nope}-{original}", {"original": "a"}) == "-a"


def test_render_survives_broken_braces():
    assert render_template("{unclosed", {"x": 1}) == "{unclosed"


def test_natural_sort_numbers():
    files = ["file10.mp4", "file2.mp4", "File1.mp4"]
    assert natural_sorted(files) == ["File1.mp4", "file2.mp4", "file10.mp4"]


def test_nat_key_is_comparable():
    assert nat_key("a2") < nat_key("a10")


def test_illegal_characters_replaced():
    out = build_name(original='bad/na:me*?"<>|.mp4', mime="video/mp4", seq=1, index=1,
                     total=1, opt=NamingOptions())
    for ch in ILLEGAL:
        assert ch not in out


def test_emoji_stripped_by_default_kept_on_request():
    a = build_name(original="🔥Great🔥.mp4", mime="video/mp4", seq=1, index=1, total=1,
                   opt=NamingOptions())
    assert "🔥" not in a
    b = build_name(original="🔥Great🔥.mp4", mime="video/mp4", seq=1, index=1, total=1,
                   opt=NamingOptions(keep_emoji=True, strip_emoji=False))
    assert "🔥" in b


def test_names_are_byte_limited_and_keep_extension():
    name = build_name(original="x" * 400 + ".pdf", mime="application/pdf", seq=1,
                      index=1, total=1, opt=NamingOptions())
    assert byte_len(name) <= 255
    assert name.endswith(".pdf")


def test_utf8_truncation_never_splits_a_character():
    assert truncate_bytes("🔥🔥🔥", 4) == "🔥"


def test_fit_name_keeps_extension_even_when_ext_is_long():
    out = fit_name("a" * 300, "verylongextensionname", limit=20)
    assert byte_len(out) <= 20


def test_find_replace_plain_and_regex():
    opt = NamingOptions(find="Module", replace="Ch")
    assert apply_transform("Module 1", opt) == "Ch 1"
    opt2 = NamingOptions(find=r"\d+", replace="N", regex=True)
    assert apply_transform("part 12", opt2) == "part N"


def test_broken_regex_is_ignored():
    opt = NamingOptions(find="([", replace="x", regex=True)
    assert apply_transform("abc", opt) == "abc"


def test_case_transforms():
    for mode, want in (("upper", "ABC"), ("lower", "abc"), ("title", "Abc"), ("keep", "aBC")):
        assert apply_transform("aBC", NamingOptions(case=mode)) == want


def test_prefix_suffix_and_tag():
    opt = NamingOptions(prefix="P-", suffix="-S", tag_enabled=True, tag="@chan")
    out = build_name(original="a.mp4", mime="video/mp4", seq=1, index=1, total=1, opt=opt)
    assert out.startswith("P-") and out.endswith(".mp4")
    assert "@chan" in out and "-S" in out


def test_global_tag_off_by_default():
    out = build_name(original="a.mp4", mime="video/mp4", seq=1, index=1, total=1,
                     opt=NamingOptions(tag="@chan"))
    assert "@chan" not in out


def test_space_replacement():
    opt = NamingOptions(space="_")
    assert "_" in build_name(original="big file name.mp4", mime="video/mp4", seq=1,
                             index=1, total=1, opt=opt)


def test_ext_rule_force_and_infer():
    forced = NamingOptions(ext_rule="force", force_ext="mkv")
    assert build_name(original="a.avi", mime="video/x-msvideo", seq=1, index=1, total=1,
                      opt=forced).endswith(".mkv")
    inferred = NamingOptions(ext_rule="infer")
    assert build_name(original="a.unknown", mime="video/mp4", seq=1, index=1, total=1,
                      opt=inferred, head=b"\x00\x00\x00\x18ftypisom").endswith(".mp4")


def test_mime_and_magic_detection():
    assert ext_from_mime("video/x-matroska") == "mkv"
    assert ext_from_mime("application/pdf; charset=x") == "pdf"
    assert sniff_ext(b"%PDF-1.7") == "pdf"
    assert sniff_ext(b"\x89PNG\r\n\x1a\n") == "png"
    assert sniff_ext(b"ID3\x04") == "mp3"
    assert sniff_ext(b"zzzz") == ""
    assert infer_ext(claimed="bin", mime="image/jpeg", head=b"") == "jpg"
    assert infer_ext(claimed="dat", mime="", head=b"%PDF-") == "pdf"
    assert infer_ext(claimed="keep.me", mime="", head=b"") == "keep.me"


def test_split_ext_edge_cases():
    assert split_ext("a.mp4") == ("a", "mp4")
    assert split_ext(".hidden") == (".hidden", "")
    assert split_ext("noext") == ("noext", "")


def test_sanitize_collapses_spaces_and_strips_dots():
    assert sanitize("  a    b  ") == "a b"
    assert sanitize("...name...") == "name"
    assert sanitize("a..b") == "a.b"


def test_strip_emoji_only_removes_emoji():
    assert strip_emoji("hello 🔥 world ✅") .strip() == "hello  world"


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.00 KB"
    assert human_size(1536 * 1024) == "1.50 MB"
    assert human_size(5 * 1024 ** 3) == "5.00 GB"


def test_dedupe_rename_skip_overwrite():
    assert dedupe_names(["a.mp4", "a.mp4"], "rename") == {0: "a.mp4", 1: "a (1).mp4"}
    assert dedupe_names(["a.mp4", "a.mp4"], "skip") == {0: "a.mp4", 1: ""}
    assert dedupe_names(["a.mp4", "a.mp4"], "overwrite") == {0: "a.mp4", 1: "a.mp4"}


def test_dedupe_case_insensitive_and_triple():
    out = dedupe_names(["A.mp4", "a.MP4", "A.mp4"], "rename")
    assert len({v.lower() for v in out.values()}) == 3


def test_preview_rows_marks_duplicates():
    items = [{"name": "a.mp4", "size": 1, "mime": "video/mp4"},
             {"name": "a.mp4", "size": 1, "mime": "video/mp4"}]
    rows = preview_rows(items, NamingOptions())
    assert rows[0]["final"] == "a.mp4"
    assert rows[1]["duplicate"] is True
    assert rows[1]["final"] == "a (1).mp4"


def test_options_roundtrip_dict():
    o = NamingOptions(template="{seq}", start=5, case="upper", tag="x")
    back = NamingOptions.from_dict(o.to_dict())
    assert back == o
    assert NamingOptions.from_dict({"unknown": 1}).template == "{original}.{ext}"
    assert NamingOptions.from_dict(None).template == "{original}.{ext}"


def test_start_and_step_shape_the_numbers():
    opt = NamingOptions(template="{seq:03d}", start=10, step=5)
    seqs = [build_name(original="a.mp4", mime="", seq=opt.start + i * opt.step, index=i + 1,
                       total=3, opt=opt) for i in range(3)]
    assert seqs == ["010.mp4", "015.mp4", "020.mp4"]
