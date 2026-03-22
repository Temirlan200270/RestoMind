"""Фильтр стоп-листа по терминальной группе (ответ iiko Cloud)."""

from app.services.menu_sync import _collect_stopped_product_ids_from_iiko_response


def test_collect_all_groups_when_no_filter() -> None:
    stop_data = {
        "terminalGroupStopLists": [
            {
                "organizationId": "org-1",
                "items": [
                    {
                        "terminalGroupId": "aaaa-1111",
                        "items": [{"productId": "p1", "balance": 0}],
                    },
                    {
                        "terminalGroupId": "bbbb-2222",
                        "items": [{"productId": "p2", "balance": 0}],
                    },
                ],
            },
        ],
    }
    ids, total, used = _collect_stopped_product_ids_from_iiko_response(stop_data, "")
    assert ids == {"p1", "p2"}
    assert total == 2
    assert used == 2


def test_collect_single_terminal_group() -> None:
    stop_data = {
        "terminalGroupStopLists": [
            {
                "organizationId": "org-1",
                "items": [
                    {
                        "terminalGroupId": "aaaa-1111",
                        "items": [{"productId": "p1", "balance": 0}],
                    },
                    {
                        "terminalGroupId": "bbbb-2222",
                        "items": [{"productId": "p2", "balance": 0}],
                    },
                ],
            },
        ],
    }
    ids, total, used = _collect_stopped_product_ids_from_iiko_response(stop_data, "bbbb-2222")
    assert ids == {"p2"}
    assert total == 2
    assert used == 1


def test_uuid_compare_case_insensitive() -> None:
    stop_data = {
        "terminalGroupStopLists": [
            {
                "organizationId": "org-1",
                "items": [
                    {
                        "terminalGroupId": "AAAA-1111-2222-3333-444444444444",
                        "items": [{"productId": "px", "balance": 0}],
                    },
                ],
            },
        ],
    }
    ids, _, used = _collect_stopped_product_ids_from_iiko_response(
        stop_data,
        "aaaa-1111-2222-3333-444444444444",
    )
    assert ids == {"px"}
    assert used == 1
