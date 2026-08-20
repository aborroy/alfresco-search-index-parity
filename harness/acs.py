"""Alfresco Content Services REST client, limited to what the harness needs."""

import urllib.parse

from . import httpjson

DEFAULT_BASE = "http://localhost:8080"
DEFAULT_AUTH = ("admin", "admin")


class Acs:
    def __init__(self, base=DEFAULT_BASE, auth=DEFAULT_AUTH):
        self.base = base.rstrip("/")
        self.auth = auth
        self.core = self.base + "/alfresco/api/-default-/public/alfresco/versions/1"

    def _call(self, method, path, **kwargs):
        kwargs.setdefault("auth", self.auth)
        return httpjson.request(method, self.core + path, **kwargs)[1]

    # --- lifecycle ---

    def is_ready(self):
        status, _ = httpjson.request(
            "GET", self.core + "/probes/-ready-", auth=self.auth, timeout=15, accept_status=(503,)
        )
        return status == 200

    def wait_ready(self, timeout=900):
        httpjson.wait_until(
            self.is_ready, timeout=timeout, interval=10, description="the repository to be ready"
        )

    # --- nodes ---

    def node(self, node_id, include=None):
        query = "?include=" + ",".join(include) if include else ""
        return self._call("GET", "/nodes/%s%s" % (node_id, query))["entry"]

    def node_by_path(self, relative_path):
        quoted = urllib.parse.quote(relative_path)
        return self._call("GET", "/nodes/-root-?relativePath=" + quoted)["entry"]

    def create_node(
        self, parent_id, name, node_type="cm:content", properties=None, aspects=None
    ):
        payload = {"name": name, "nodeType": node_type}
        if properties:
            payload["properties"] = properties
        if aspects:
            payload["aspectNames"] = aspects
        return self._call("POST", "/nodes/%s/children" % parent_id, payload=payload)["entry"]

    def update_node(self, node_id, payload):
        return self._call("PUT", "/nodes/%s" % node_id, payload=payload)["entry"]

    def set_content(self, node_id, content, content_type="text/plain"):
        return self._call(
            "PUT", "/nodes/%s/content" % node_id, data=content, content_type=content_type
        )["entry"]

    def delete_node(self, node_id, permanent=True):
        suffix = "?permanent=true" if permanent else ""
        self._call("DELETE", "/nodes/%s%s" % (node_id, suffix))

    def rename(self, node_id, new_name):
        return self.update_node(node_id, {"name": new_name})

    def add_tag(self, node_id, tag):
        return self._call("POST", "/nodes/%s/tags" % node_id, payload=[{"tag": tag}])

    # --- content model ---

    def deploy_model(self, model_xml, file_name):
        """Deploy and activate a content model the way an administrator would: upload it into
        Data Dictionary/Models as a cm:dictionaryModel node, then set cm:modelActive.
        """
        models_folder = self.node_by_path("Data Dictionary/Models")
        existing = self._find_child(models_folder["id"], file_name)
        if existing:
            self.delete_node(existing["id"])
        node = self.create_node(
            models_folder["id"],
            file_name,
            node_type="cm:dictionaryModel",
            properties={"cm:modelActive": False},
        )
        self.set_content(node["id"], model_xml, content_type="text/xml")
        return self.update_node(node["id"], {"properties": {"cm:modelActive": True}})

    def namespace_prefix_map(self):
        """The URI to prefix map for every deployed model, straight from the dictionary.

        Served by the model-ns-prefix-mapping addon, which compose.yaml mounts into the
        repository webapp. The response is exactly the structure the batch indexer consumes as
        `alfresco.reindex.prefixes-file`, so nothing has to be assembled by hand.
        """
        url = self.base + "/alfresco/s/model/ns-prefix-map"
        try:
            _, body = httpjson.request("GET", url, auth=self.auth, timeout=60)
        except httpjson.HttpError as error:
            if error.status == 404:
                raise RuntimeError(
                    "%s is not available: the model-ns-prefix-mapping addon is not installed "
                    "in this repository" % url
                ) from None
            raise
        return body["prefixUriMap"]

    def _find_child(self, parent_id, name):
        listing = self._call("GET", "/nodes/%s/children?maxItems=1000" % parent_id)
        for entry in listing["list"]["entries"]:
            if entry["entry"]["name"] == name:
                return entry["entry"]
        return None

    def child_by_name(self, parent_id, name):
        return self._find_child(parent_id, name)
