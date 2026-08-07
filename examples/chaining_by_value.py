"""Chained Actors must hand over values, not dataset ids.

An Actor running with standard permissions cannot read the default dataset of a different run:
the API answers "Insufficient permissions". Locally this never shows up, because your personal
token reads everything you own. I lost a day to that before confirming it on a cloud run.

An agent wires tools the way your schema suggests. If sourceDatasetId sits at the top with a
confident description, that is the path it takes, and it gets an error it cannot fix.

    python3 examples/chaining_by_value.py
"""
import json


class InsufficientPermissions(Exception):
    """What the platform raises when a run reads another run's dataset."""


def read_foreign_dataset(dataset_id):
    """Stand-in for the real API call. On the platform this is what happens with standard
    permissions, every time, no matter how valid the id is."""
    raise InsufficientPermissions("dataset %s belongs to another run" % dataset_id)


def load_input(inp):
    """Values first, reference second, and the reason for the order stated out loud.

    Returns (items, note). A refusal is described rather than returned as an empty list:
    the caller sees the output, never the log.
    """
    items = inp.get("items") or []
    if items:
        return items, "loaded %d records passed by value" % len(items)

    dataset_id = inp.get("sourceDatasetId")
    if not dataset_id:
        return [], "no input: pass items, or a dataset this run can read"

    try:
        return read_foreign_dataset(dataset_id), "loaded from dataset %s" % dataset_id
    except InsufficientPermissions as exc:
        return [], ("dataset handoff failed (%s). Pass the records in `items` instead: "
                    "a run cannot read another run's dataset." % exc)


def mark_checkable(sellers):
    """The upstream Actor says what the downstream one can do with each record.

    Sellers without a tax ID used to travel down the chain as empty strings, come back with no
    risk data, and read as "checked and clean". They had never been checked.
    """
    out = []
    for s in sellers:
        has_inn = bool((s.get("inn") or "").strip())
        out.append({**s,
                    "checkable": has_inn,
                    "checkableReason": None if has_inn
                    else "no legal entity published by the marketplace"})
    return out


if __name__ == "__main__":
    sellers = [
        {"sellerId": "99819", "name": "Store A", "inn": "7707083893"},
        {"sellerId": "1269372", "name": "Store B", "inn": ""},
    ]
    marked = mark_checkable(sellers)
    for s in marked:
        print(json.dumps(s, ensure_ascii=False))
    print()

    for label, inp in (("by reference", {"sourceDatasetId": "3Kh66VT9e0fN4H52M"}),
                       ("by value", {"items": [s for s in marked if s["checkable"]]})):
        items, note = load_input(inp)
        print("%-13s -> %d records | %s" % (label, len(items), note))
