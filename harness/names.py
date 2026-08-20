"""Index field names.

Both indexers name their fields through the same encoder, so the harness has to reproduce it
exactly to look documents up. `encode` is a Python transcription of
AlfrescoQualifiedNameTranslator.encode: Java URLEncoder in x-www-form-urlencoded form, then
four characters that are legal in a URI but troublesome in a search field name are escaped by
hand.
"""

# Fields written by the indexers, independent of the content model.
ALIVE = "ALIVE"
READER = "READER"
DENIED = "DENIED"
OWNER = "OWNER"
ASPECT = "ASPECT"
TYPE = "TYPE"
PATH = "PATH"
UNPREFIXED_PATH = "UNPREFIXED_PATH"
PROPERTIES = "PROPERTIES"
TAG = "TAG"
APATH = "APATH"
ANAME = "ANAME"
PNAME = "PNAME"
NPATH = "NPATH"
PARENT = "PARENT"
PRIMARY_PARENT = "PRIMARYPARENT"
PRIMARY_HIERARCHY = "primaryHierarchy"
ANCESTOR = "ANCESTOR"
STANDARD_ANCESTOR = "STANDARD_ANCESTOR"
CATEGORY_ANCESTOR = "CATEGORY_ANCESTOR"

METADATA_LAST_UPDATE = "METADATA_INDEXING_LAST_UPDATE"
CONTENT_LAST_UPDATE = "CONTENT_INDEXING_LAST_UPDATE"
PATH_LAST_UPDATE = "PATH_INDEXING_LAST_UPDATE"

# Written only by the batch and reindexing applications: the timestamp a reindexing run
# started, used by the guard script that keeps a reindexer from overwriting fresher data.
REINDEXING_START_TIME = "reindexingStartTime"

_KEEP = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-*")


def encode(qualified_name):
    """Encode a prefixed QName the way both indexers do: `cm:name` -> `cm%3Aname`."""
    out = []
    for char in qualified_name:
        if char in _KEEP:
            out.append(char)
        elif char == " ":
            out.append("+")
        else:
            out.extend("%%%02X" % byte for byte in char.encode("utf-8"))
    encoded = "".join(out)
    return (
        encoded.replace(".", "%2E")
        .replace("-", "%2D")
        .replace("*", "%2A")
        .replace("+", "%20")
    )


# Content model fields the harness refers to by name.
NAME = encode("cm:name")
CREATED = encode("cm:created")
MODIFIED = encode("cm:modified")
CREATOR = encode("cm:creator")
MODIFIER = encode("cm:modifier")
CONTENT = encode("cm:content")
CONTENT_MIMETYPE = encode("cm:content.mimetype")
CONTENT_SIZE = encode("cm:content.size")
CONTENT_ENCODING = encode("cm:content.encoding")

# Fields contributed by model/parity-model.xml, whose namespace no Alfresco distribution
# knows about.
PARITY_SERIAL = encode("parity:serial")
PARITY_SCORE = encode("parity:score")
PARITY_REVIEWED = encode("parity:reviewed")
PARITY_REVIEW_DATE = encode("parity:reviewDate")
PARITY_CLASSIFICATION = encode("parity:classification")
