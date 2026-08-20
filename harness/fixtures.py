"""The repository content the checks are measured against.

Each fixture exists to exercise a different part of the write path: paths and ancestry, text
extraction through the transform service, tags resolved from the tag repository, aspects, and a
custom content model whose namespace the indexer has never heard of.
"""

from . import stack

ROOT_FOLDER_NAME = "index-parity"
MODEL_FILE_NAME = "parity-model.xml"

CUSTOM_NAMESPACE_URI = "http://parity.example.org/model/1.0"
CUSTOM_NAMESPACE_PREFIX = "parity"

# Distinctive enough that finding it in the index proves text extraction ran, rather than
# something else having put a plausible string there.
CONTENT_NEEDLE = "parityneedle"
TEXT_BODY = (
    "The quick brown fox jumps over the lazy dog.\n"
    "This document exists so that content extraction has something to extract: %s.\n"
) % CONTENT_NEEDLE


def deploy_model(acs):
    model_xml = (stack.ROOT / "model" / MODEL_FILE_NAME).read_text(encoding="utf-8")
    return acs.deploy_model(model_xml, MODEL_FILE_NAME)


def seed(acs):
    """Create the fixture tree and return a label -> node mapping.

    Labels are stable across runs and across repositories, which is what makes two snapshots
    comparable.
    """
    root = _ensure_root(acs)
    nodes = {"root": root}

    folder = acs.create_node(root["id"], "folder-plain", node_type="cm:folder")
    nodes["folder"] = folder

    subfolder = acs.create_node(folder["id"], "folder-nested", node_type="cm:folder")
    nodes["subfolder"] = subfolder

    nodes["doc-text"] = _document(acs, folder["id"], "plain.txt")
    nodes["doc-deep"] = _document(acs, subfolder["id"], "deep.txt")

    nodes["doc-titled"] = _document(
        acs,
        root["id"],
        "titled.txt",
        aspects=["cm:titled"],
        properties={"cm:title": "Parity title", "cm:description": "Parity description"},
    )

    tagged = _document(acs, root["id"], "tagged.txt")
    acs.add_tag(tagged["id"], "parity-tag")
    nodes["doc-tagged"] = acs.node(tagged["id"], include=["properties", "aspectNames"])

    # Two shapes of custom model use, because the indexer treats them differently: a node whose
    # own type comes from the custom namespace, and an ordinary cm:content node that merely
    # carries a custom aspect and property.
    nodes["doc-custom-type"] = _document(
        acs,
        root["id"],
        "custom-record.txt",
        node_type="parity:record",
        aspects=["parity:classified"],
        properties={
            "parity:serial": "SERIAL-0001",
            "parity:score": 42,
            "parity:reviewed": True,
            "parity:reviewDate": "2026-01-15T00:00:00.000+0000",
            "parity:classification": "internal",
        },
    )

    nodes["doc-custom-aspect"] = _document(
        acs,
        root["id"],
        "custom-aspect.txt",
        aspects=["parity:classified"],
        properties={"parity:classification": "confidential"},
    )

    nodes["doc-sentinel"] = _document(acs, root["id"], "sentinel.txt")
    nodes["doc-control"] = _document(acs, root["id"], "control.txt")
    nodes["doc-doomed"] = _document(acs, root["id"], "doomed.txt")

    return nodes


def _ensure_root(acs):
    existing = acs.child_by_name("-root-", ROOT_FOLDER_NAME)
    if existing:
        acs.delete_node(existing["id"])
    return acs.create_node("-root-", ROOT_FOLDER_NAME, node_type="cm:folder")


def _document(acs, parent_id, name, node_type="cm:content", aspects=None, properties=None):
    node = acs.create_node(
        parent_id, name, node_type=node_type, aspects=aspects, properties=properties
    )
    acs.set_content(node["id"], TEXT_BODY)
    return acs.node(node["id"], include=["properties", "aspectNames", "path"])
