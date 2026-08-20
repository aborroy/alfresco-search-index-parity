"""What a document in the index has to look like, and what may legitimately differ.

The required sets below are the fields an Enterprise live indexing deployment reads back:
identity, ancestry, permissions and the content model values. A Community-written document that
carries all of them is a document Enterprise live indexing can keep updating in place, because
both indexers write them through the same encoder, the same field names and the same upsert
scripts.
"""

from . import names

# Present on every live node, whatever its type.
REQUIRED_ALWAYS = [
    names.ALIVE,
    names.TYPE,
    names.ASPECT,
    names.NAME,
    names.CREATED,
    names.MODIFIED,
    names.CREATOR,
    names.MODIFIER,
    names.OWNER,
    names.READER,
    names.DENIED,
    names.PROPERTIES,
    names.TAG,
    names.METADATA_LAST_UPDATE,
]

# Written by the path writer. Enterprise live indexing has a dedicated path indexing service
# for these; on Community the same fields come from the batch application's path writer. Losing
# them would break PATH, ANCESTOR and site scoping in every query, so they are required.
REQUIRED_PATH = [
    names.PATH,
    names.UNPREFIXED_PATH,
    names.PARENT,
    names.PRIMARY_PARENT,
    names.PRIMARY_HIERARCHY,
    names.STANDARD_ANCESTOR,
    names.CATEGORY_ANCESTOR,
    names.PATH_LAST_UPDATE,
]

# Mapped by the repository, written by neither indexer. They are part of the schema and of no
# document, which is why the harness reports them rather than requiring them: the connector code
# that both indexers share defines no constant and no writer for any of them, so an index that
# Enterprise fills does not have them either.
MAPPED_BUT_UNWRITTEN = [names.APATH, names.ANAME, names.PNAME, names.NPATH]

# Written when a node has a content property.
REQUIRED_CONTENT = [
    names.CONTENT_MIMETYPE,
    names.CONTENT_SIZE,
    names.CONTENT_ENCODING,
]

# Written once text extraction has succeeded.
REQUIRED_EXTRACTED_TEXT = [
    names.CONTENT,
    names.CONTENT_LAST_UPDATE,
]

# label -> which of the sets above apply
EXPECTATIONS = {
    "root": ("folder",),
    "folder": ("folder",),
    "subfolder": ("folder",),
    "doc-text": ("document", "text"),
    "doc-deep": ("document", "text"),
    "doc-titled": ("document", "text"),
    "doc-tagged": ("document", "text"),
    "doc-custom-type": ("document", "text"),
    "doc-custom-aspect": ("document", "text"),
    "doc-sentinel": ("document", "text"),
    "doc-control": ("document", "text"),
    "doc-doomed": ("document", "text"),
}

# A node whose own type belongs to a namespace the indexer cannot resolve never reaches the
# index at all, so it cannot be part of the contract until the namespace is configured.
CUSTOM_TYPE_LABELS = {"doc-custom-type"}


def required_fields(kinds):
    required = list(REQUIRED_ALWAYS) + list(REQUIRED_PATH)
    if "document" in kinds:
        required += REQUIRED_CONTENT
    if "text" in kinds:
        required += REQUIRED_EXTRACTED_TEXT
    return required


# Fields whose value is a timestamp of when indexing happened, or an identifier local to one
# repository. They are excluded from snapshot value comparison; their presence is still
# compared.
VOLATILE_FIELDS = {
    names.METADATA_LAST_UPDATE,
    names.CONTENT_LAST_UPDATE,
    names.PATH_LAST_UPDATE,
    names.REINDEXING_START_TIME,
    names.CREATED,
    names.MODIFIED,
    names.PATH,
    names.UNPREFIXED_PATH,
    names.APATH,
    names.NPATH,
    names.PARENT,
    names.PRIMARY_PARENT,
    names.PRIMARY_HIERARCHY,
    names.ANCESTOR,
    names.STANDARD_ANCESTOR,
    names.CATEGORY_ANCESTOR,
    names.TAG,
    "DBID",
    "TXID",
    "LID",
}

# Fields only a reindexing run writes. Their presence in an index that Enterprise will take
# over is residue, not incompatibility: the mapping does not declare them, so they are stored
# in _source and never queried.
BATCH_ONLY_FIELDS = {names.REINDEXING_START_TIME}

# Indices only the Community batch application creates, alongside the shared `alfresco` index.
BATCH_ONLY_INDICES = ["alfresco-reindex-state", "alfresco-reindex-dead-letter"]
