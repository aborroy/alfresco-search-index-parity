# Method

The claim under test is narrow and worth stating precisely.

> An OpenSearch index populated by Alfresco Search Community (the
> `alfresco-elasticsearch-batch-indexing` application) is the same index that Alfresco Search
> Enterprise live indexing (`alfresco-elasticsearch-indexing`) reads and continues to write, so
> an upgrade from Community to Enterprise does not require rebuilding it from scratch.

"The same index" decomposes into four things that have to hold, and each check below tests one
of them on a running system rather than by reading code.

1. The mapping is not the indexer's opinion. If each indexer created its own schema, two
   indexers could disagree about field types and the second one would inherit an index it cannot
   use.
2. Documents are addressed the same way. If the two indexers keyed documents differently, live
   indexing would write duplicates instead of updating what is already there.
3. Documents carry the same fields. A field Enterprise queries but Community never wrote is a
   query that silently returns nothing after the upgrade.
4. Concurrent or overlapping writes are safe. During a cutover both indexers may touch the same
   documents, and the older writer must not undo the newer one.

## Check by check

### schema-owner

The harness starts the repository, the database, the transform service and OpenSearch, and
deliberately does not start any indexer: the indexer sits behind a Compose profile, so it does
not merely lag, it does not exist. It then asserts that the `alfresco` index is already there,
that its mapping is `dynamic: false`, that the core fields (`ALIVE`, `PATH`, `READER`,
`METADATA_INDEXING_LAST_UPDATE`) are mapped, and that the index holds zero documents.

An index that exists, is fully mapped and is empty can only have been created by the
repository. That is the whole point: the schema belongs to Alfresco Content Services, which
creates it on startup when `elasticsearch.createIndexIfNotExists=true`, and neither indexer is
in a position to define a competing one.

`dynamic: false` matters twice over. It is why the check can be strict about the mapping, and it
is why anything an indexer writes that the mapping does not declare is stored but never indexed,
and therefore cannot affect a query.

### model-drives-mapping

Still with no indexer running, the harness deploys `model/parity-model.xml` the way an
administrator would: as a `cm:dictionaryModel` node in Data Dictionary/Models with
`cm:modelActive` set to true. It then waits for the custom fields to appear in the index
mapping and reports which fields the deployment added.

This shows the schema tracking the content model with no indexer involved at all. The
consequence for an upgrade is that the index an Enterprise deployment inherits was mapped from
the same content model it will be reading, not from whatever the Community indexer happened to
write.

### document-contract

Eleven fixtures cover folders, nested folders, documents with text content, an aspect with
properties, a tag, and the two custom-model shapes. For each one the harness fetches the
OpenSearch document whose `_id` is the node identifier reported by the repository REST API, and
requires the fields listed in `harness/contract.py`: identity and permissions
(`ALIVE`, `TYPE`, `cm%3Aname`, `OWNER`, `READER`), ancestry (`PATH`, `APATH`, `ANAME`, `PNAME`,
`NPATH`, `PARENT`, `PRIMARYPARENT`, `primaryHierarchy`), content metadata, and the indexing
timestamps.

Fetching by node identifier is itself the second part of the claim: documents are keyed by the
bare node UUID, so a live indexing update addresses the document that is already there.

The ancestry fields are required rather than optional on purpose. On Community they come from
the batch application's path writer, which is enabled by default and can be turned off. An
index written with path indexing disabled would pass a naive field-count comparison and still
break `PATH`, `ANCESTOR` and site scoping for every query.

### content-extraction

Every fixture with content contains a distinctive token. The check requires that token inside
the encoded `cm:content` field, which is the same field Enterprise fills through the Transform
Service and the Shared File Store. Community gets the text from the repository endpoint
`/alfresco/service/api/solr/textContent` instead, so this check is about the destination, not
the route: two different extraction paths, one field.

### custom-model-prefix

An indexer reading the repository database has no access to the dictionary, so it turns namespace
URIs into field-name prefixes using a static JSON file, `alfresco.reindex.prefixes-file`. That is
true of the Community batch application and of the Enterprise reindexing application alike; only
Enterprise live indexing is exempt, because repository events already carry prefixed names. The
file therefore stays relevant after the upgrade rather than being a Community detail to discard.

The repository is the only component that knows every
deployed model, so the file is generated from it: the harness downloads the
[model-ns-prefix-mapping](https://github.com/AlfrescoLabs/model-ns-prefix-mapping) addon,
checksum-verified, mounts it into the repository webapp, and writes what
`/alfresco/s/model/ns-prefix-map` returns to `generated/prefixes.json`.

The experiment is then differential, and the two states are the two states a real installation
passes through. Fetch the map before the model is deployed and index with it, which is what any
installation looks like when a model is added and the map is not refreshed, and record what the
index holds for the two custom-model nodes. Then re-fetch the map, restart the indexer, touch both
nodes so they fall after the indexing cursor, and record again.

Two fixtures rather than one, because the cost depends on where the unknown namespace appears:
`doc-custom-aspect` is an ordinary `cm:content` node carrying a custom aspect and property,
while `doc-custom-type` is a node whose own type comes from the custom namespace. The check also
records the indexer's own error lines and the dead letter index count, so the report says what
the application did about it.

### mapping-untouched

The set of mapped fields is captured just before the indexer starts and again after it has
written every fixture, including the restart with a different configuration. Any difference
would mean the indexer contributes to the schema, which would make the schema a function of
which indexer ran. There is no difference.

### live-indexing-takeover

This is the check that a shared index is actually safe, and it needs a stand-in for Enterprise
live indexing, which is not part of a Community stack.

Both indexers stamp every document they write with `METADATA_INDEXING_LAST_UPDATE`, and both
compare that stamp before writing: the update is expressed as a Painless script that turns
itself into a no-op when the document in the index is newer than the write being attempted. So
the harness writes, straight into OpenSearch, a recognisable `cm:name` and a stamp ten years in
the future: a document that looks exactly like one live indexing has just refreshed. It then
renames the same node in the repository, which is the strongest thing the batch indexer could
do to it.

Completion is observed on a second node renamed immediately afterwards. The reindexing
application walks transactions in ascending time order, so the control node's new name appearing
in the index means the sentinel node's window has already been processed. Without that, a
passing check could just mean the harness looked too early.

If the planted values survive, the older writer respected the newer one, which is what makes a
cutover window safe in both directions.

### delete-semantics

A fixture is deleted permanently through the REST API, and the harness waits for the index to
agree: either the document is gone or it is a tombstone with `ALIVE=false`. Both are states
Enterprise live indexing also produces, and both stop the node matching queries.

### batch-residue

Informational, and deliberately so. It catalogues what a Community-written index carries that a
live-indexed one would not: `reindexingStartTime` on the documents, and the
`alfresco-reindex-state` and `alfresco-reindex-dead-letter` indices next to the shared one.
None of it is declared in the mapping, so none of it is queryable. It is what an Enterprise
deployment inherits and ignores.

## What this method cannot show

- It does not run Alfresco Search Enterprise. Everything above is measured on a Community stack
  and compared against the field contract Enterprise depends on, plus the guard behaviour that
  makes a shared index safe. The direct comparison needs an entitlement; see
  [enterprise-comparison.md](enterprise-comparison.md), which the `snapshot` and `diff` commands
  exist for.
- It says nothing about whether a given Enterprise version accepts a given OpenSearch or
  Elasticsearch cluster. Document compatibility and engine support are separate questions, and
  the second one is answered by the supported platforms documentation, not by an experiment.
- It tests the versions pinned in `.env`. Both indexers are versioned independently of the
  repository, and a claim about one pair of versions is not a claim about all of them. Re-run it
  against the versions you intend to upgrade to.
- It is a functional check on a laptop-sized repository. Nothing here measures how long
  reindexing takes at scale.
