"""The checks.

Every check answers one question that has to be true for a Community-written index to be
usable by Alfresco Search Enterprise live indexing, and every check reports the evidence it
saw rather than just a verdict.
"""

import time

from . import contract, fixtures, httpjson, names, stack


class Result:
    PASS = "PASS"
    FAIL = "FAIL"
    INFO = "INFO"

    def __init__(self, key, question, status, summary, evidence=None):
        self.key = key
        self.question = question
        self.status = status
        self.summary = summary
        self.evidence = evidence or {}

    @property
    def ok(self):
        return self.status != Result.FAIL

    def as_dict(self):
        return {
            "check": self.key,
            "question": self.question,
            "status": self.status,
            "summary": self.summary,
            "evidence": self.evidence,
        }


def _flat(value):
    """Index values arrive as scalars or single-element lists; compare them the same way."""
    if isinstance(value, list):
        return value[0] if len(value) == 1 else value
    return value


def refreshed_document(search, doc_id):
    search.refresh()
    return search.document(doc_id)


def mapped_fields(mapping):
    return set((mapping.get("properties") or {}).keys())


# --- 1. Who creates the index ---


def check_schema_owner(search):
    """The repository creates the index and its mapping. No indexer is running yet."""
    evidence = {"indexerContainerExists": stack.indexer_container_exists()}
    if evidence["indexerContainerExists"]:
        return Result(
            "schema-owner",
            "Is the index created by the repository rather than by an indexer?",
            Result.FAIL,
            "the batch indexer container already exists, so this check cannot attribute the index",
            evidence,
        )
    if not search.index_exists():
        return Result(
            "schema-owner",
            "Is the index created by the repository rather than by an indexer?",
            Result.FAIL,
            "the repository did not create the index",
            evidence,
        )

    mapping = search.mapping()
    fields = mapped_fields(mapping)
    settings = search.settings()
    evidence.update(
        {
            "dynamic": mapping.get("dynamic"),
            "mappedFieldCount": len(fields),
            "documentCount": search.count(),
            "shards": settings["index"]["number_of_shards"],
            "replicas": settings["index"]["number_of_replicas"],
            "coreFieldsPresent": sorted(
                field
                for field in (
                    names.ALIVE,
                    names.PATH,
                    names.APATH,
                    names.READER,
                    names.TYPE,
                    names.METADATA_LAST_UPDATE,
                    names.NAME,
                )
                if field in fields
            ),
        }
    )
    problems = []
    if str(evidence["dynamic"]).lower() != "false":
        problems.append("mapping is not dynamic:false, it is %r" % evidence["dynamic"])
    if evidence["documentCount"] != 0:
        problems.append("index already holds %d documents" % evidence["documentCount"])
    for field in (names.ALIVE, names.PATH, names.READER, names.METADATA_LAST_UPDATE):
        if field not in fields:
            problems.append("core field %s is not mapped" % field)

    if problems:
        return Result(
            "schema-owner",
            "Is the index created by the repository rather than by an indexer?",
            Result.FAIL,
            "; ".join(problems),
            evidence,
        )
    return Result(
        "schema-owner",
        "Is the index created by the repository rather than by an indexer?",
        Result.PASS,
        "the repository created the index with %d mapped fields and dynamic:false before any "
        "indexer existed" % len(fields),
        evidence,
    )


def check_model_drives_mapping(before_fields, after_fields):
    """Deploying a content model extends the mapping, with no indexer involved."""
    added = sorted(after_fields - before_fields)
    expected = [
        names.PARITY_SERIAL,
        names.PARITY_SCORE,
        names.PARITY_REVIEWED,
        names.PARITY_REVIEW_DATE,
        names.PARITY_CLASSIFICATION,
    ]
    missing = [field for field in expected if field not in after_fields]
    evidence = {
        "fieldsAddedByModelDeployment": added,
        "expectedCustomFields": expected,
        "missingCustomFields": missing,
    }
    question = "Does deploying a content model extend the index mapping without an indexer?"
    if missing:
        return Result(
            "model-drives-mapping",
            question,
            Result.FAIL,
            "the repository did not map %s" % ", ".join(missing),
            evidence,
        )
    return Result(
        "model-drives-mapping",
        question,
        Result.PASS,
        "the repository mapped all %d custom model fields itself, adding %d fields in total"
        % (len(expected), len(added)),
        evidence,
    )


def check_mapping_unchanged_by_indexer(before_fields, after_fields):
    """The indexer writes into the mapping it was given and adds nothing to it."""
    added = sorted(after_fields - before_fields)
    removed = sorted(before_fields - after_fields)
    evidence = {"addedByIndexer": added, "removedByIndexer": removed}
    question = "Does the Community indexer leave the mapping exactly as the repository made it?"
    if added or removed:
        return Result(
            "mapping-untouched",
            question,
            Result.FAIL,
            "the mapping changed while the indexer ran: %d added, %d removed"
            % (len(added), len(removed)),
            evidence,
        )
    return Result(
        "mapping-untouched",
        question,
        Result.PASS,
        "the mapping is byte-for-byte the one the repository created; the indexer added no "
        "fields of its own",
        evidence,
    )


# --- 2. What the documents contain ---


def check_document_contract(search, nodes):
    """Every fixture is one document, keyed by its node id, carrying the required fields."""
    search.refresh()
    per_label = {}
    problems = []
    for label, kinds in contract.EXPECTATIONS.items():
        node = nodes[label]
        document = search.document(node["id"])
        if document is None:
            problems.append("%s: no document with _id %s" % (label, node["id"]))
            per_label[label] = {"id": node["id"], "found": False}
            continue
        missing = [field for field in contract.required_fields(kinds) if field not in document]
        per_label[label] = {
            "id": node["id"],
            "found": True,
            "fieldCount": len(document),
            "missingRequired": missing,
            names.TYPE: _flat(document.get(names.TYPE)),
        }
        if missing:
            problems.append("%s: missing %s" % (label, ", ".join(missing)))

    evidence = {"documents": per_label}
    question = "Is every node one document, keyed by node id, with the fields Enterprise reads?"
    if problems:
        return Result(
            "document-contract", question, Result.FAIL, "; ".join(problems), evidence
        )
    return Result(
        "document-contract",
        question,
        Result.PASS,
        "all %d fixtures are present as documents keyed by their node id, each carrying every "
        "required field" % len(per_label),
        evidence,
    )


def check_content_extraction(search, nodes):
    """Extracted text lands in the same cm:content field Enterprise fills through ATS."""
    search.refresh()
    labels = [
        label for label, kinds in contract.EXPECTATIONS.items() if "text" in kinds
    ]
    found = {}
    problems = []
    for label in labels:
        document = search.document(nodes[label]["id"]) or {}
        text = _flat(document.get(names.CONTENT)) or ""
        found[label] = {
            "hasNeedle": fixtures.CONTENT_NEEDLE in text,
            "mimetype": _flat(document.get(names.CONTENT_MIMETYPE)),
            "size": _flat(document.get(names.CONTENT_SIZE)),
            "transformStatus": _flat(document.get(names.encode("cm:content.tr_status"))),
        }
        if not found[label]["hasNeedle"]:
            problems.append(label)

    evidence = {"documents": found, "needle": fixtures.CONTENT_NEEDLE}
    question = "Does extracted text land in the same content field Enterprise writes?"
    if problems:
        return Result(
            "content-extraction",
            question,
            Result.FAIL,
            "no extracted text in %s for %s" % (names.CONTENT, ", ".join(problems)),
            evidence,
        )
    return Result(
        "content-extraction",
        question,
        Result.PASS,
        "extracted text is in %s for all %d documents with content" % (names.CONTENT, len(labels)),
        evidence,
    )


# --- 3. The custom model namespace ---


TYPE_FIELDS = [
    names.PARITY_SERIAL,
    names.PARITY_SCORE,
    names.PARITY_REVIEWED,
    names.PARITY_REVIEW_DATE,
    names.PARITY_CLASSIFICATION,
]


def observe_custom_model(search, nodes, dead_letter_count, log_excerpt):
    """Record what the index holds for the two custom-model nodes, and why."""
    search.refresh()
    typed = search.document(nodes["doc-custom-type"]["id"])
    aspected = search.document(nodes["doc-custom-aspect"]["id"])
    return {
        "typedNode": {
            "indexed": typed is not None,
            "presentFields": sorted(field for field in TYPE_FIELDS if field in (typed or {})),
            "absentFields": sorted(field for field in TYPE_FIELDS if field not in (typed or {})),
            names.TYPE: _flat((typed or {}).get(names.TYPE)),
        },
        "aspectNode": {
            "indexed": aspected is not None,
            "classificationIndexed": names.PARITY_CLASSIFICATION in (aspected or {}),
            "classification": _flat((aspected or {}).get(names.PARITY_CLASSIFICATION)),
            names.TYPE: _flat((aspected or {}).get(names.TYPE)),
            names.ASPECT: (aspected or {}).get(names.ASPECT),
        },
        "deadLetterDocuments": dead_letter_count,
        "indexerErrors": log_excerpt,
    }


def indexer_error_excerpt(limit=16):
    """The indexer's own account of what it could not resolve."""
    lines = [
        line.strip()
        for line in stack.logs(tail=4000).splitlines()
        if "impossible to" in line
    ]
    unique = list(dict.fromkeys(line.split("--- ")[-1] for line in lines))
    return unique[:limit]


def check_custom_model_prefix(stale, refetched):
    """The one configuration item that decides what custom model content is searchable.

    An application that reads the repository database resolves namespace URIs through a static
    file, so a namespace absent from that file cannot be turned into a field name. What that
    costs depends on where the unknown namespace appears: a property is dropped, but a node whose
    own type is unknown cannot be indexed at all. The Enterprise reindexing application reads the
    same property, so this is not only a Community concern.

    Measured against a map the repository generated before the model was deployed, which is the
    state of any installation where a model was added and the map was not refreshed, and then
    against the same map re-fetched afterwards.

    Either way the damage is fidelity, not shape: the documents that do exist are keyed and
    structured exactly as before, and a current map fills in the rest.
    """
    evidence = {
        "namespace": fixtures.CUSTOM_NAMESPACE_URI,
        "withMapPredatingTheModel": stale,
        "withMapRefetchedAfterTheModel": refetched,
    }
    question = "What does a content model namespace missing from the indexer's prefix map cost?"

    recovered = (
        refetched["typedNode"]["indexed"]
        and not refetched["typedNode"]["absentFields"]
        and refetched["aspectNode"]["classificationIndexed"]
    )
    if not recovered:
        missing = []
        if not refetched["typedNode"]["indexed"]:
            missing.append("the custom-typed node is still not indexed")
        if refetched["typedNode"]["absentFields"]:
            missing.append("still missing " + ", ".join(refetched["typedNode"]["absentFields"]))
        if not refetched["aspectNode"]["classificationIndexed"]:
            missing.append("the custom aspect property is still missing")
        return Result(
            "custom-model-prefix",
            question,
            Result.FAIL,
            "a prefix map covering %s did not repair the index: %s"
            % (fixtures.CUSTOM_NAMESPACE_URI, "; ".join(missing)),
            evidence,
        )

    losses = []
    if not stale["typedNode"]["indexed"]:
        losses.append("a node of a custom type was not indexed at all")
    elif stale["typedNode"]["absentFields"]:
        losses.append(
            "a node of a custom type lost %d field(s)" % len(stale["typedNode"]["absentFields"])
        )
    if stale["aspectNode"]["indexed"] and not stale["aspectNode"]["classificationIndexed"]:
        losses.append("a cm:content node with a custom aspect was indexed without that property")

    if not losses:
        return Result(
            "custom-model-prefix",
            question,
            Result.INFO,
            "nothing: this version indexed the custom model correctly with a prefix map that "
            "predates it",
            evidence,
        )
    return Result(
        "custom-model-prefix",
        question,
        Result.PASS,
        "with a prefix map predating the model, %s, and %d node(s) were dead-lettered; "
        "re-fetching the map from the repository recovered everything"
        % (" and ".join(losses), stale["deadLetterDocuments"]),
        evidence,
    )


# --- 4. Handing the index over to live indexing ---


SENTINEL_NAME = "SENTINEL-written-as-if-by-live-indexing.txt"
TEN_YEARS_MS = 10 * 365 * 24 * 60 * 60 * 1000


def check_live_indexing_takeover(acs, search, nodes):
    """Prove the guard script that makes a shared index safe.

    Both indexers stamp METADATA_INDEXING_LAST_UPDATE and both compare it before writing. To
    stand in for a document that Enterprise live indexing has just refreshed, the harness
    writes a far-future stamp and a recognisable name straight into the index, then changes the
    same node in the repository. If the batch writer respected the stamp, the planted values
    survive; if it clobbered them, they do not.

    Completion is observed on a second node changed afterwards: the reindexer walks
    transactions in ascending time order, so the control node appearing means the sentinel
    node's window has already been processed.
    """
    sentinel_id = nodes["doc-sentinel"]["id"]
    control_id = nodes["doc-control"]["id"]
    far_future = int(time.time() * 1000) + TEN_YEARS_MS

    search.update_fields(
        sentinel_id, {names.NAME: SENTINEL_NAME, names.METADATA_LAST_UPDATE: far_future}
    )
    planted = refreshed_document(search, sentinel_id)

    repository_name = "sentinel-renamed-by-repository.txt"
    acs.rename(sentinel_id, repository_name)
    control_name = "control-renamed-%d.txt" % int(time.time())
    acs.rename(control_id, control_name)

    def control_indexed():
        document = refreshed_document(search, control_id)
        return document and _flat(document.get(names.NAME)) == control_name

    httpjson.wait_until(
        control_indexed,
        timeout=300,
        interval=5,
        description="the indexer to pick up the control node rename",
    )

    after = refreshed_document(search, sentinel_id) or {}
    evidence = {
        "plantedName": SENTINEL_NAME,
        "plantedStamp": far_future,
        "plantedStampWasStored": _flat(planted.get(names.METADATA_LAST_UPDATE)) == far_future,
        "repositoryRenamedNodeTo": repository_name,
        "nameInIndexAfterwards": _flat(after.get(names.NAME)),
        "stampInIndexAfterwards": _flat(after.get(names.METADATA_LAST_UPDATE)),
        "controlNodeName": control_name,
        "controlNodeIndexed": True,
    }
    question = "Can a Community indexer overwrite data that live indexing wrote more recently?"
    if _flat(after.get(names.NAME)) == SENTINEL_NAME:
        return Result(
            "live-indexing-takeover",
            question,
            Result.PASS,
            "no: the batch writer saw a newer METADATA_INDEXING_LAST_UPDATE and left the "
            "document untouched, so the two indexers can write to one index",
            evidence,
        )
    return Result(
        "live-indexing-takeover",
        question,
        Result.FAIL,
        "yes: the planted name was replaced by %r, so the guard did not hold"
        % _flat(after.get(names.NAME)),
        evidence,
    )


# --- 5. Deletions ---


def check_delete_semantics(acs, search, nodes):
    """A deleted node has to stop matching queries, whichever indexer removed it."""
    doomed_id = nodes["doc-doomed"]["id"]
    before = refreshed_document(search, doomed_id)
    acs.delete_node(doomed_id, permanent=True)

    def gone_or_dead():
        document = refreshed_document(search, doomed_id)
        if document is None:
            return {"documentRemoved": True}
        if _flat(document.get(names.ALIVE)) is False:
            return {"documentRemoved": False, "alive": False, "type": _flat(document.get(names.TYPE))}
        return None

    question = "Does a delete leave the index in a state Enterprise would also produce?"
    try:
        outcome = httpjson.wait_until(
            gone_or_dead,
            timeout=300,
            interval=5,
            description="the indexer to process the deletion",
        )
    except TimeoutError as error:
        return Result(
            "delete-semantics",
            question,
            Result.FAIL,
            str(error),
            {"documentBeforeDelete": bool(before), "documentAfterDelete": True},
        )
    outcome["documentBeforeDelete"] = bool(before)
    return Result(
        "delete-semantics",
        question,
        Result.PASS,
        "the deletion reached the index: %s"
        % ("the document was removed" if outcome.get("documentRemoved") else "the document was "
           "turned into a tombstone with ALIVE=false"),
        outcome,
    )


# --- 6. What the Community indexer leaves behind ---


def check_batch_residue(search, nodes):
    """Catalogue everything a Community-written index carries that a live-indexed one does not.

    This is informational on purpose. None of it is declared in the mapping, so none of it is
    queryable; it is what an Enterprise deployment would inherit and ignore.
    """
    search.refresh()
    mapping_fields = mapped_fields(search.mapping())
    document = search.document(nodes["doc-text"]["id"]) or {}
    residue = sorted(field for field in contract.BATCH_ONLY_FIELDS if field in document)
    unmapped = sorted(field for field in document if field not in mapping_fields)
    indices = search.alfresco_indices()
    side_indices = {
        name: {"docs": int(indices[name].get("docs.count") or 0)}
        for name in contract.BATCH_ONLY_INDICES
        if name in indices
    }
    unexpected_indices = sorted(
        name
        for name in indices
        if name != "alfresco" and name not in contract.BATCH_ONLY_INDICES
    )
    return Result(
        "batch-residue",
        "What does a Community-written index carry that a live-indexed one does not?",
        Result.INFO,
        "documents carry %d batch-only field(s) (%s), all unmapped and therefore not queryable; "
        "%d side index(es) sit next to the shared index"
        % (len(residue), ", ".join(residue) or "none", len(side_indices)),
        {
            "batchOnlyFieldsInDocument": residue,
            "unmappedFieldsInDocument": unmapped,
            "sideIndices": side_indices,
            "otherAlfrescoIndices": unexpected_indices,
            "sharedIndexDocumentCount": search.count(),
        },
    )


def check_mapped_but_unwritten(search, nodes):
    """Fields the repository maps that no document carries.

    Reported rather than required. The connector code both indexers share defines neither a
    constant nor a writer for these, so they are absent from an Enterprise-filled index too;
    treating them as missing data would invent a difference that does not exist.
    """
    search.refresh()
    document = search.document(nodes["doc-text"]["id"]) or {}
    mapping_fields = mapped_fields(search.mapping())
    unwritten = sorted(
        field
        for field in contract.MAPPED_BUT_UNWRITTEN
        if field in mapping_fields and field not in document
    )
    written = sorted(field for field in contract.MAPPED_BUT_UNWRITTEN if field in document)
    return Result(
        "mapped-but-unwritten",
        "Which mapped fields does no indexer populate?",
        Result.INFO,
        "%d mapped field(s) are populated by neither indexer (%s)"
        % (len(unwritten), ", ".join(unwritten) or "none"),
        {"mappedAndUnwritten": unwritten, "unexpectedlyWritten": written},
    )
