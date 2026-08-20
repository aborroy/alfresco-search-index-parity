# Comparing against Alfresco Search Enterprise directly

The checks in this repository run on a Community stack alone. They establish that a
Community-written index satisfies the contract Enterprise live indexing depends on, and that the
guard both indexers share makes a shared index safe. What they cannot do is put the two indices
side by side, because `alfresco-elasticsearch-indexing` requires an Alfresco Enterprise
entitlement and its images are not publicly pullable.

If you have that entitlement, the `snapshot` and `diff` commands turn the question into a file
comparison. Two ways to run it, in increasing order of strength.

## A. Same repository, two indexers (strongest)

Both indices describe the same nodes, so node identifiers line up and field values can be
compared, not just field names.

1. Run the harness normally and keep the stack up:

   ```bash
   python3 -m harness run
   python3 -m harness snapshot --label community-batch-indexing --out snapshots/community.json
   ```

   `generated/fixture-ids.json` now holds the node identifiers of the fixtures. Keep it: it is
   what makes the second capture comparable.

2. Point a second, empty OpenSearch index at the same repository and let Enterprise live
   indexing fill it. The reindexing application from the Enterprise connector is the component
   that does the historical pass; the live indexing services keep it current from that point on.
   Give it its own index name, or its own cluster, so that the Community-written index survives
   for comparison.

3. Capture it, against the same repository so that the fixture identifiers still resolve:

   ```bash
   python3 -m harness --opensearch http://localhost:9201 \
       snapshot --label enterprise-live-indexing --out snapshots/enterprise.json
   ```

4. Compare, values included:

   ```bash
   python3 -m harness diff snapshots/community.json snapshots/enterprise.json --values
   ```

The exit code is 0 when the only differences are the ones this repository documents as residue.

## B. Two repositories, same fixtures (weaker but easier)

If a second repository is easier to arrange than a second index against one repository, seed it
with the same fixtures and compare field names only. Drop `--values`: node identifiers, paths and
timestamps legitimately differ between repositories, and comparing them would produce noise
rather than findings.

```bash
python3 -m harness diff snapshots/community.json snapshots/enterprise.json
```

## Reading the diff

`diff` reports three things:

- **mapping**: mapped fields present in only one index. Should be empty. The repository creates
  the mapping, so a difference here points at different repository versions or different
  search-related repository settings, not at the indexers.
- **documents**: per fixture, fields present in only one capture, and with `--values` the fields
  whose values differ. Fields listed in `BATCH_ONLY_FIELDS` in `harness/contract.py` are
  expected on the Community side and ignored by the verdict.
- **sideIndices**: indices next to `alfresco`. The Community application adds
  `alfresco-reindex-state` and `alfresco-reindex-dead-letter`.

Volatile fields are excluded from value comparison and listed in `VOLATILE_FIELDS` in
`harness/contract.py`: indexing timestamps, audit dates, and everything carrying a node
identifier or a path. Presence is still compared for all of them; only the values are skipped.

## Before you cut over

Two things this repository measures are worth acting on rather than just reading.

- **Namespaces.** Check `alfresco.reindex.prefixes-file` against the content models actually
  deployed in your repository before trusting a Community-built index. The `custom-model-prefix`
  check in `report.json` shows what an unlisted namespace costs. The Enterprise reindexing
  application configures the same property, so carry the generated file across the upgrade instead
  of regenerating it from memory.
- **The gap.** The Community application indexes up to a watermark. Whatever was committed
  between that watermark and the moment live indexing takes over has to be covered by a bounded
  reindexing pass over that window, with an overlap, rather than assumed.
