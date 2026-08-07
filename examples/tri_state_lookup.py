"""Absence of a finding and absence of a check are different answers.

The Actor this came from looks companies up in a public registry. The registry sits behind a
certificate chain no default trust store carries, so requests die with CERTIFICATE_VERIFY_FAILED.
My first version caught that, logged a warning and returned an empty result - which is exactly
what the Actor returns when a company genuinely is not in the registry.

A human reads the warning. An agent running two hundred companies sees the same [] both times
and turns it into a business fact. Half of those facts were wrong.

    python3 examples/tri_state_lookup.py
"""
import json


class RegistryUnreachable(Exception):
    """Raised when the source did not answer. Not the same as an empty answer."""


def fetch_registry(inn):
    """Stand-in for the real lookup. 7707083893 is listed, 5000000000 is not,
    and 7736207543 stands for the source failing on us."""
    if inn == "7736207543":
        raise RegistryUnreachable("registry TLS chain rejected")
    return [{"product": "Some listed product", "class": "06.09"}] if inn == "7707083893" else []


def check_company(inn):
    """One company, one record, and the record always says why it looks the way it does."""
    try:
        products = fetch_registry(inn)
    except RegistryUnreachable as exc:
        # The caller never sees our log, so the reason goes into the record itself.
        return {
            "inn": inn,
            "foundInRegistry": None,          # None, not False: nobody looked
            "productCount": None,             # None, not 0: absence of a count
            "lookupStatus": "source_unreachable",
            "lookupDetail": str(exc),
        }
    return {
        "inn": inn,
        "foundInRegistry": bool(products),
        "productCount": len(products),
        "lookupStatus": "ok" if products else "not_found",
        "lookupDetail": None,
    }


def summarise(records):
    """What an agent would compute. Note that the unreachable row is excluded rather than
    counted as a zero: averaging None into a score is how a failed check becomes a clean bill."""
    checked = [r for r in records if r["foundInRegistry"] is not None]
    unchecked = [r for r in records if r["foundInRegistry"] is None]
    return {
        "companiesChecked": len(checked),
        "listed": sum(1 for r in checked if r["foundInRegistry"]),
        "notListed": sum(1 for r in checked if not r["foundInRegistry"]),
        "couldNotCheck": len(unchecked),
    }


if __name__ == "__main__":
    records = [check_company(i) for i in ("7707083893", "5000000000", "7736207543")]
    for r in records:
        print(json.dumps(r, ensure_ascii=False))
    print()
    print("summary an agent can trust:", json.dumps(summarise(records)))
    print()
    print("The old version returned [] for both 5000000000 and 7736207543,")
    print("and the summary above would have claimed two companies were checked and clean.")
