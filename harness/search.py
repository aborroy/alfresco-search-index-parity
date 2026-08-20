"""OpenSearch client, limited to what the harness needs.

Two of the checks deliberately write to the index from outside the indexer: one to stand in
for a document that Enterprise live indexing has just refreshed, so the guard script in the
batch writer has something newer to refuse to overwrite.
"""

from . import httpjson

DEFAULT_BASE = "http://localhost:9200"

INDEX = "alfresco"
WATERMARK_INDEX = "alfresco-reindex-state"
DEAD_LETTER_INDEX = "alfresco-reindex-dead-letter"


class Search:
    def __init__(self, base=DEFAULT_BASE):
        self.base = base.rstrip("/")

    def _call(self, method, path, **kwargs):
        return httpjson.request(method, self.base + path, **kwargs)

    def is_ready(self):
        status, body = self._call("GET", "/_cluster/health", timeout=15)
        return status == 200 and body.get("status") in ("green", "yellow")

    def wait_ready(self, timeout=300):
        httpjson.wait_until(
            self.is_ready, timeout=timeout, interval=5, description="OpenSearch to be healthy"
        )

    def indices(self):
        """All indices including hidden ones: the reindexing side indices are hidden."""
        _, body = self._call("GET", "/_cat/indices?format=json&expand_wildcards=all")
        return {entry["index"]: entry for entry in body}

    def alfresco_indices(self):
        return {name: entry for name, entry in self.indices().items() if name.startswith(INDEX)}

    def index_exists(self, index=INDEX):
        status, _ = self._call("HEAD", "/" + index, accept_status=(404,))
        return status == 200

    def mapping(self, index=INDEX):
        _, body = self._call("GET", "/%s/_mapping" % index)
        return body[index]["mappings"]

    def settings(self, index=INDEX):
        _, body = self._call("GET", "/%s/_settings" % index)
        return body[index]["settings"]

    def count(self, index=INDEX, query=None):
        payload = {"query": query} if query else None
        status, body = self._call(
            "POST", "/%s/_count" % index, payload=payload, accept_status=(404,)
        )
        return 0 if status == 404 else body["count"]

    def document(self, doc_id, index=INDEX):
        """Return the stored _source, or None when the document is absent."""
        status, body = self._call("GET", "/%s/_doc/%s" % (index, doc_id), accept_status=(404,))
        if status == 404 or not body.get("found"):
            return None
        return body["_source"]

    def refresh(self, index=INDEX):
        self._call("POST", "/%s/_refresh" % index, accept_status=(404,))

    def search(self, payload, index=INDEX):
        _, body = self._call("POST", "/%s/_search" % index, payload=payload)
        return body

    def update_fields(self, doc_id, fields, index=INDEX):
        """Partial update from outside the indexers, used to plant test state."""
        self._call(
            "POST", "/%s/_update/%s?refresh=true" % (index, doc_id), payload={"doc": fields}
        )

    def all_documents(self, index=INDEX, size=500):
        body = self.search({"query": {"match_all": {}}, "size": size}, index=index)
        return {hit["_id"]: hit["_source"] for hit in body["hits"]["hits"]}
