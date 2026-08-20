# Alfresco search index parity

Can an OpenSearch index built by **Alfresco Search Community** (the `alfresco-elasticsearch-batch-indexing`
application) be handed over to **Alfresco Search Enterprise** live indexing (`alfresco-elasticsearch-indexing`)
when a Community installation is upgraded, without reindexing from scratch?

This is a runnable answer. It starts a Community stack with Docker Compose, indexes a set of
fixture nodes, and then interrogates the resulting index against the contract that live indexing
expects. It also captures the index to a file, so anyone entitled to run Enterprise can index the
same content with the other indexer and diff the two captures directly.

Nothing here is a document about what the code should do. Every number below came out of a run.

## Answer

Yes, with one condition you must satisfy before the switch.

The index is not the indexer's. The repository creates it, owns its mapping, and extends that
mapping when you deploy a content model. Both indexers write into that same schema using the same
field-name encoder and the same guarded upsert scripts, so a Community-written document is a
document live indexing can keep updating in place. The batch application leaves behind a small
amount of residue that is not part of the mapping and therefore not queryable.

The condition: **every custom content model namespace must be listed in the indexer's namespace
prefix map.** If it is not, nodes are silently dropped or silently stripped of their custom
properties, with nothing in the dead letter queue to tell you. This is a property of indexing from
the database, not of Community: Enterprise reindexing reads the same map, so the file you generate
for the Community indexer is the file the Enterprise reindexing pass will want as well. See
[The custom namespace trap](#the-custom-namespace-trap).

## Results

From `report.json`, produced by `python3 -m harness run` against ACS 26.2.0, batch indexer 5.7.1,
OpenSearch 2.19.6.

| Check | Result | What it establishes |
| --- | --- | --- |
| `schema-owner` | PASS | The repository created the index with 951 mapped fields, `dynamic: false`, 1 shard, 1 replica and 0 documents, while no indexer container existed. |
| `model-drives-mapping` | PASS | Deploying a content model the indexer has never seen added all 5 custom property fields to the mapping (9 entries, including `_untokenized` variants), again with no indexer running. |
| `custom-model-prefix` | PASS | Measures the cost of a prefix map that predates the content model, and that re-fetching it from the repository recovers everything. |
| `document-contract` | PASS | All 12 fixtures are single documents keyed by bare node UUID, each carrying every field live indexing reads: identity, ancestry, permissions, tags, content model values and the three indexing watermarks. |
| `content-extraction` | PASS | Extracted text is in `cm%3Acontent` for all 9 documents with content, the same field Enterprise fills through the Transform Service. |
| `mapping-untouched` | PASS | The indexer added and removed exactly zero mapped fields. |
| `live-indexing-takeover` | PASS | A document stamped as if live indexing had just refreshed it survived a repository rename untouched: the batch writer's guard script no-oped instead of overwriting fresher data. |
| `delete-semantics` | PASS | A permanent delete produces a tombstone with `ALIVE=false`, not a removed document. |
| `batch-residue` | INFO | One batch-only field per document (`reindexingStartTime`), unmapped and so not queryable, plus 2 hidden side indices (`alfresco-reindex-state`, 1 doc; `alfresco-reindex-dead-letter`, 0 docs). No other `alfresco*` index. |
| `mapped-but-unwritten` | INFO | 4 mapped fields are populated by neither indexer: `ANAME`, `APATH`, `NPATH`, `PNAME`. |

`live-indexing-takeover` is the check that carries the most weight, because it is the one that
would make a mixed index dangerous rather than merely different. The harness writes a
`METADATA_INDEXING_LAST_UPDATE` ten years in the future plus a recognisable name into a document
from outside both indexers, then renames that node in the repository. The stamp stored afterwards
was still `2102597322712` and the name was still the planted one, so the batch writer read the
stamp, decided its own work was stale and declined the write. That is the same guard Enterprise
live indexing relies on, keyed on the same field.

## The custom namespace trap

The repository maps custom model fields by itself. An indexer that reads the database resolves a
node's type and property names through a static prefix map instead, and that map is only as current
as whoever last generated it. Alfresco Search Enterprise live indexing is the exception, because
repository events already carry prefixed names, but the Enterprise reindexing application that has
to fill the index before live indexing takes over reads the same map from the same property.
The harness fetches the map from the repository before deploying a model in
`http://parity.example.org/model/1.0`, which is the state of any installation where a model was
deployed and the map was not refreshed, and indexes two shapes of node with it:

- A node whose own **type** is custom (`parity:record`) was **not indexed at all**. No document,
  no error to the caller, and `0` documents in the dead letter queue. The indexer logged
  `impossible to retrieve type name for node 875` and counted it as a filtered item.
- An ordinary `cm:content` node merely **carrying** a custom aspect was indexed, but without
  `parity%3Aclassification`, and with its `ASPECT` field down to
  `["cm:author", "cm:titled", "cm:auditable"]`. The custom aspect had vanished from the document.

Re-fetching the map now that the model is deployed, restarting the indexer and touching both nodes
recovered everything: the typed node appeared with `TYPE: parity:record` and all 5 custom fields,
and the aspect node came back with
`ASPECT: ["parity:classified", "cm:author", "cm:titled", "sys:cascadeUpdate", "cm:auditable"]` and
its classification value.

Both failures are silent. Neither surfaces as a failed job, an HTTP error or a dead letter. An
index that looks healthy can be missing entire node types.

The repository generates the map, through the
[model-ns-prefix-mapping](https://github.com/AlfrescoLabs/model-ns-prefix-mapping) addon, whose
`/alfresco/s/model/ns-prefix-map` endpoint returns every namespace in the dictionary in exactly the
structure the indexer consumes. The harness downloads the addon, checksum-verified, mounts it into
the repository webapp, and writes what it returns to `generated/prefixes.json`. The indexer is
pointed at that file with a JVM system property, because it is consumed through a Spring
`@PropertySource` and an environment variable does not reach it:

```yaml
JAVA_OPTS: -Dalfresco.reindex.prefixes-file=file:/config/prefixes.json
```

Fetching the whole map matters, because the file replaces the map rather than extending it.
Supplying only the custom namespace strips Alfresco's own, and the indexer then cannot resolve
`sys:versionMajor` while validating the repository schema: `validateDbSchemaStep` fails with
`NumberFormatException: Cannot parse null string`, the watermark never advances, and nothing is
indexed at all. Passing one entry as `-DprefixUriMap[uri]=prefix` fails the same way, since the
system property takes precedence over the whole map instead of adding a key to it. A missing core
namespace therefore fails loudly, while a missing custom namespace fails silently.

The map the repository generates is also not the one the indexer image ships: on Community 26.2 it
holds 64 namespaces against the shipped 60, missing four that only exist in Enterprise deployments
and adding eight the repository registers, mostly IPTC and XMP metadata namespaces.

## Quick start

Requirements: Docker with Compose v2, roughly 8 GB free for the containers, and Python 3.9 or
later. No Python packages to install; the harness is standard library only.

```bash
python3 -m harness run
```

A full run takes about five minutes on a warm image cache. It tears down any previous stack so the
index is always created from scratch, starts the repository without an indexer, runs the checks in
dependency order, writes `report.json` and `snapshots/community.json`, and leaves the stack up.
Exit status is non-zero if any check fails.

Other subcommands:

```bash
python3 -m harness up                        # stack without the indexer
python3 -m harness down                       # stop and delete volumes
python3 -m harness prepare                    # fetch generated/prefixes.json from the repository
python3 -m harness snapshot --label mine --out snapshots/mine.json
python3 -m harness diff snapshots/community.json snapshots/mine.json --values
```

`--acs` and `--opensearch` override the endpoints if you point the harness at something other
than the bundled stack.

## Comparing against Enterprise

The checks answer the question from the Community side alone, which is what most people can run.
If you are entitled to Alfresco Search Enterprise, `docs/enterprise-comparison.md` gives the
procedure for the direct comparison: index the same repository with each indexer, capture both,
and diff. The diff reports mapping differences, per-document field differences and, with
`--values`, differing values, ignoring timestamps and the known residue. It exits non-zero and
names the blocking differences when the two indices are not interchangeable.

## Before you upgrade a real installation

1. Generate the indexer's prefix map from the repository being indexed, after every content model
   is deployed, and confirm every custom namespace URI is in it. This is the only issue the harness
   found that can lose data. Keep the file after the upgrade: the Enterprise reindexing pass reads
   the same property.
2. If any namespace was missing, the affected nodes need reindexing after you refresh the map.
   Nothing in the index tells you which they are.
3. Expect `reindexingStartTime` in `_source` on documents the batch application wrote. It is not
   mapped, so nothing queries it.
4. Decide what to do with `alfresco-reindex-state` and `alfresco-reindex-dead-letter`. Live
   indexing does not read them. Keeping the watermark document is the safer default until the
   switch is proven, since it is what a resumed batch run uses to know where it stopped.
5. Mind the gap between the last batch cycle and the moment live indexing starts consuming events.
   Nodes modified inside that window are in neither stream.

## What this does not prove

- It does not run Enterprise live indexing. It verifies the Community index against the contract
  derived from the shared connector code, and provides the tooling for the direct comparison.
- It says nothing about which query syntax each search subsystem supports. That is a separate
  question about the query side, not the index side.
- It tests the pinned versions in `.env` and nothing else. Both indexers are free to change the
  schema they write in any release.
- It is not a performance test. Twelve nodes prove shape, not throughput.

## Layout

```
compose.yaml                     repository, database, transforms, OpenSearch, and the indexer
                                 behind a profile so it can be started separately
.env                             pinned image tags
model/parity-model.xml           custom content model in a namespace no indexer knows
harness/                         the checks, as a Python package with no dependencies
  names.py                       index field names and the field-name encoder
  contract.py                    which fields a document must have, and what may differ
  checks.py                      one function per check, each returning evidence
  snapshot.py                    capture an index to a file, and diff two captures
  stack.py                       Compose driver, and the prefix-map addon it downloads
  fixtures.py                    the 12 nodes under test
docs/method.md                   what each check does and why it is enough
docs/enterprise-comparison.md    procedure for diffing against a live-indexed index
```

Run artifacts (`report.json`, `snapshots/*.json`, `generated/`) are not tracked.

## Versions under test

| Component | Version |
| --- | --- |
| Alfresco Content Services Community | 26.2.0 |
| Alfresco Search Community batch indexing | 5.7.1 |
| OpenSearch | 2.19.6 |
| Alfresco Transform Core AIO | 5.4.3 |
| PostgreSQL | 17.9 |
| model-ns-prefix-mapping addon | 1.2.0 |

The batch indexing image is published for `linux/amd64` only and runs under emulation on Apple
silicon, which is slower but works.

No Alfresco source or resource file is copied into this repository. The namespace prefix map is
generated by the repository at run time, and the addon that serves it is downloaded and
checksum-verified rather than vendored.

## License

Apache License 2.0. See `LICENSE`.
