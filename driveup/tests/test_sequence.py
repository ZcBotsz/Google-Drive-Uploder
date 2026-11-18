"""Sequence engine — pure functions (milestone 5)."""
from __future__ import annotations

from driveup.core.sequence import (MODES, apply_mode, move, move_bottom, move_top,
                                   parse_message_link, parse_range, reverse,
                                   sort_items)


def items(*ids, names=None, sizes=None, msg_ids=None):
    names = names or [f"file{i}.mp4" for i in ids]
    sizes = sizes or [i * 100 for i in ids]
    msg_ids = msg_ids or list(ids)
    return [{"id": str(i), "name": n, "size": s, "msg_id": m}
            for i, n, s, m in zip(ids, names, sizes, msg_ids)]


def ids_of(lst):
    return [x["id"] for x in lst]


def test_telegram_order_sorts_by_message_id():
    lst = items(7, 3, 9, 1, 5)
    assert ids_of(sort_items(lst, "telegram_order")) == ["1", "3", "5", "7", "9"]


def test_name_natural_sorts_2_before_10():
    lst = items(1, 2, 3, names=["part10.mp4", "part2.mp4", "part1.mp4"])
    assert [x["name"] for x in sort_items(lst, "name_natural")] == \
        ["part1.mp4", "part2.mp4", "part10.mp4"]


def test_size_sorts():
    lst = items(1, 2, 3, sizes=[300, 100, 200])
    assert [x["size"] for x in sort_items(lst, "size_asc")] == [100, 200, 300]
    assert [x["size"] for x in sort_items(lst, "size_desc")] == [300, 200, 100]


def test_date_mode_uses_msg_id_when_no_date():
    lst = items(1, 2, 3)
    assert ids_of(sort_items(lst, "date")) == ["1", "2", "3"]


def test_manual_order_wins_and_unknown_ids_go_last():
    lst = items(1, 2, 3)
    out = sort_items(lst, "manual", manual_order=["3", "1"])
    assert ids_of(out)[:2] == ["3", "1"]


def test_apply_mode_renumbers_from_one():
    lst = items(5, 2)
    out = apply_mode(lst, "telegram_order")
    assert [o["seq_index"] for o in out] == [1, 2]
    assert ids_of(out) == ["2", "5"]


def test_apply_mode_does_not_mutate_input():
    lst = items(2, 1)
    apply_mode(lst, "telegram_order")
    assert ids_of(lst) == ["2", "1"]


def test_move_drag_and_drop():
    lst = items(1, 2, 3, 4)
    assert ids_of(move(lst, "4", 0)) == ["4", "1", "2", "3"]
    assert ids_of(move(lst, "1", 2)) == ["2", "3", "1", "4"]
    assert ids_of(move(lst, "1", 99)) == ["2", "3", "4", "1"]
    assert ids_of(move(lst, "missing", 0)) == ["1", "2", "3", "4"]


def test_move_top_and_bottom():
    lst = items(1, 2, 3)
    assert ids_of(move_top(lst, "3")) == ["3", "1", "2"]
    assert ids_of(move_bottom(lst, "1")) == ["2", "3", "1"]


def test_reverse_renumbers():
    lst = items(1, 2, 3)
    out = reverse(lst)
    assert ids_of(out) == ["3", "2", "1"]
    assert [o["seq_index"] for o in out] == [1, 2, 3]


def test_parse_range_forms():
    assert parse_range("100-103") == [100, 101, 102, 103]
    assert parse_range("103-100") == [100, 101, 102, 103]
    assert parse_range("5,7,9-10") == [5, 7, 9, 10]
    assert parse_range("5,5,5") == [5]
    assert parse_range("") == []
    assert parse_range("abc") == []
    assert len(parse_range("1-9000")) == 5001        # hard cap


def test_parse_message_link_private():
    got = parse_message_link("https://t.me/c/123456/100-102")
    assert got["chat_id"] == -100123456
    assert got["ids"] == [100, 101, 102]


def test_parse_message_link_public():
    got = parse_message_link("t.me/mychannel/10,12")
    assert got["username"] == "mychannel" and got["ids"] == [10, 12]


def test_parse_message_link_rejects_junk():
    assert parse_message_link("https://example.com/x") is None
    assert parse_message_link("t.me/") is None
    assert parse_message_link("") is None


def test_every_mode_is_known():
    assert set(MODES) == {"telegram_order", "name_natural", "size_asc", "size_desc",
                          "date", "manual"}


def test_unknown_mode_falls_back_to_telegram_order():
    lst = items(9, 2)
    assert ids_of(sort_items(lst, "nonsense")) == ["2", "9"]
