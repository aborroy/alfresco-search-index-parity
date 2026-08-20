"""Capture an index as a file, and compare two captures.

The checks answer the compatibility question on a Community stack alone. The snapshot pair
answers it by direct comparison, for anyone entitled to run Alfresco Search Enterprise: index
the same repository twice, once with each indexer, and diff the captures.

Two comparison depths, because they need different setups:

  field names only  two separate repositories are enough. Node identifiers and paths differ,
                    but the set of fields per document must not.
  field values too  requires both indexers to have indexed the same repository database, so
                    that node identifiers line up. Use --values for that case.
"""

import json

from . import contract


def capture(search, nodes, label, meta=None):
    documents = {}
    for fixture_label, node in nodes.items():
        source = search.document(node["id"])
        if source is None:
            documents[fixture_label] = None
            continue
        documents[fixture_label] = {
            "id": node["id"],
            "fields": {key: source[key] for key in sorted(source)},
        }
    mapping = search.mapping()
    return {
        "capturedBy": label,
        "meta": meta or {},
        "index": {
            "dynamic": mapping.get("dynamic"),
            "fields": sorted((mapping.get("properties") or {}).keys()),
            "documentCount": search.count(),
            "otherIndices": sorted(
                name for name in search.alfresco_indices() if name != "alfresco"
            ),
        },
        "documents": documents,
    }


def write(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def diff(left, right, compare_values=False):
    """Report the differences that would matter to a query, ignoring known-volatile fields."""
    report = {
        "left": left.get("capturedBy"),
        "right": right.get("capturedBy"),
        "compareValues": compare_values,
        "mapping": {
            "onlyInLeft": sorted(set(left["index"]["fields"]) - set(right["index"]["fields"])),
            "onlyInRight": sorted(set(right["index"]["fields"]) - set(left["index"]["fields"])),
            "dynamicLeft": left["index"].get("dynamic"),
            "dynamicRight": right["index"].get("dynamic"),
        },
        "documents": {},
        "sideIndices": {
            "onlyInLeft": sorted(
                set(left["index"]["otherIndices"]) - set(right["index"]["otherIndices"])
            ),
            "onlyInRight": sorted(
                set(right["index"]["otherIndices"]) - set(left["index"]["otherIndices"])
            ),
        },
    }

    for label in sorted(set(left["documents"]) | set(right["documents"])):
        left_doc = left["documents"].get(label)
        right_doc = right["documents"].get(label)
        if not left_doc or not right_doc:
            report["documents"][label] = {
                "presentInLeft": bool(left_doc),
                "presentInRight": bool(right_doc),
            }
            continue

        left_fields = set(left_doc["fields"])
        right_fields = set(right_doc["fields"])
        entry = {
            "onlyInLeft": sorted(left_fields - right_fields),
            "onlyInRight": sorted(right_fields - left_fields),
        }
        if compare_values:
            differing = {}
            for field in sorted(left_fields & right_fields):
                if field in contract.VOLATILE_FIELDS:
                    continue
                if left_doc["fields"][field] != right_doc["fields"][field]:
                    differing[field] = {
                        "left": left_doc["fields"][field],
                        "right": right_doc["fields"][field],
                    }
            entry["differingValues"] = differing
        if any(entry.get(key) for key in ("onlyInLeft", "onlyInRight", "differingValues")):
            report["documents"][label] = entry

    report["verdict"] = _verdict(report)
    return report


def _verdict(report):
    ignorable = contract.BATCH_ONLY_FIELDS
    blocking = []
    for label, entry in report["documents"].items():
        if "presentInLeft" in entry:
            blocking.append("%s: present in only one capture" % label)
            continue
        for side in ("onlyInLeft", "onlyInRight"):
            unexpected = [field for field in entry.get(side, []) if field not in ignorable]
            if unexpected:
                blocking.append("%s: %s has %s" % (label, side, ", ".join(unexpected)))
        if entry.get("differingValues"):
            blocking.append(
                "%s: %d field value(s) differ" % (label, len(entry["differingValues"]))
            )
    if report["mapping"]["onlyInLeft"] or report["mapping"]["onlyInRight"]:
        blocking.append("the two indices do not have the same mapped fields")
    return {
        "compatible": not blocking,
        "blocking": blocking,
        "ignoredAsResidue": sorted(ignorable),
    }


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarise(report):
    lines = ["%s vs %s" % (report["left"], report["right"])]
    mapping = report["mapping"]
    lines.append(
        "  mapping: %d field(s) only in left, %d only in right"
        % (len(mapping["onlyInLeft"]), len(mapping["onlyInRight"]))
    )
    if report["documents"]:
        for label, entry in sorted(report["documents"].items()):
            lines.append("  %s: %s" % (label, json.dumps(entry, sort_keys=True)))
    else:
        lines.append("  documents: no differences outside the fields listed as residue")
    verdict = report["verdict"]
    lines.append("  verdict: %s" % ("compatible" if verdict["compatible"] else "not compatible"))
    for item in verdict["blocking"]:
        lines.append("    - " + item)
    return "\n".join(lines)
