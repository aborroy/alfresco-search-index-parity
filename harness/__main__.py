"""Command line entry point: python3 -m harness [command]"""

import argparse
import json
import sys
import time
from pathlib import Path

from . import VERSION, acs, checks, contract, fixtures, httpjson, names, search, snapshot, stack

REPORT_PATH = stack.ROOT / "report.json"
DEFAULT_SNAPSHOT = stack.SNAPSHOTS / "community.json"


def log(message):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), message), flush=True)


# --- commands ---


def cmd_prepare(args):
    """Read the namespace prefix map out of the pinned indexer image and mount-ready it."""
    prefixes = stack.stock_prefixes()
    target = stack.write_prefixes(prefixes)
    log("prefix map from %s: %d namespaces -> %s" % (stack.indexer_image(), len(prefixes), target))
    return 0


def cmd_up(args):
    cmd_prepare(args)
    log("starting the repository, database, transforms and OpenSearch (no indexer yet)")
    stack.up_core()
    client = acs.Acs(args.acs)
    engine = search.Search(args.opensearch)
    engine.wait_ready()
    log("OpenSearch is healthy")
    client.wait_ready()
    log("the repository is ready")
    return 0


def cmd_down(args):
    stack.down()
    return 0


def cmd_snapshot(args):
    engine = search.Search(args.opensearch)
    nodes = _existing_fixtures()
    data = snapshot.capture(
        engine,
        nodes,
        args.label,
        meta={"harnessVersion": VERSION, "images": stack.env()},
    )
    path = snapshot.write(data, Path(args.out))
    log("captured %d documents to %s" % (len(data["documents"]), path))
    return 0


def cmd_diff(args):
    report = snapshot.diff(
        snapshot.load(Path(args.left)), snapshot.load(Path(args.right)), compare_values=args.values
    )
    print(snapshot.summarise(report))
    if args.out:
        snapshot.write(report, Path(args.out))
    return 0 if report["verdict"]["compatible"] else 1


def cmd_run(args):
    client = acs.Acs(args.acs)
    engine = search.Search(args.opensearch)
    results = []

    if not args.no_reset:
        log("tearing down any previous run, so the index is created from scratch")
        stack.down()

    cmd_up(args)

    # 1. Nothing has indexed anything yet: whatever exists in OpenSearch was made by the
    # repository.
    log("check: who created the index")
    results.append(checks.check_schema_owner(engine))
    mapping_before_model = checks.mapped_fields(engine.mapping())

    # 2. A content model the indexer knows nothing about, deployed while no indexer exists.
    log("deploying model/%s" % fixtures.MODEL_FILE_NAME)
    fixtures.deploy_model(client)
    _wait_for_mapping(engine, names.PARITY_SERIAL)
    mapping_after_model = checks.mapped_fields(engine.mapping())
    results.append(checks.check_model_drives_mapping(mapping_before_model, mapping_after_model))

    log("seeding fixtures")
    nodes = fixtures.seed(client)
    _write_fixture_ids(nodes)

    mapping_before_indexer = checks.mapped_fields(engine.mapping())

    # 3. Now let the Community indexer fill the index the repository built.
    log("starting the batch indexer with the prefix map the image ships")
    stack.up_indexer()
    _wait_for_documents(engine, nodes, exclude=contract.CUSTOM_TYPE_LABELS)
    _wait_for_text(engine, nodes, exclude=contract.CUSTOM_TYPE_LABELS)

    # 4. The custom model namespace, measured before and after configuring it.
    stock_observation = checks.observe_custom_model(
        engine,
        nodes,
        engine.count(index=search.DEAD_LETTER_INDEX),
        checks.indexer_error_excerpt(),
    )
    log(
        "with the prefix map from the image: custom-typed node indexed=%s, custom aspect "
        "property indexed=%s"
        % (
            stock_observation["typedNode"]["indexed"],
            stock_observation["aspectNode"]["classificationIndexed"],
        )
    )

    log("adding %s to the prefix map and restarting the indexer" % fixtures.CUSTOM_NAMESPACE_URI)
    stack.write_prefixes(
        stack.stock_prefixes(),
        extra={fixtures.CUSTOM_NAMESPACE_URI: fixtures.CUSTOM_NAMESPACE_PREFIX},
    )
    stack.restart_indexer()
    log("touching the custom nodes so they fall after the indexing cursor again")
    client.rename(nodes["doc-custom-type"]["id"], "custom-record-touched.txt")
    client.rename(nodes["doc-custom-aspect"]["id"], "custom-aspect-touched.txt")
    _wait_for_field(engine, nodes["doc-custom-type"]["id"], names.PARITY_SERIAL, timeout=600)
    _wait_for_field(
        engine, nodes["doc-custom-aspect"]["id"], names.PARITY_CLASSIFICATION, timeout=600
    )
    extended_observation = checks.observe_custom_model(
        engine, nodes, engine.count(index=search.DEAD_LETTER_INDEX), []
    )
    results.append(checks.check_custom_model_prefix(stock_observation, extended_observation))

    # 5. With the namespace configured, every fixture is in the index, so the contract can be
    # checked over all of them.
    _wait_for_text(engine, nodes)
    log("check: document contract")
    results.append(checks.check_document_contract(engine, nodes))
    log("check: content extraction")
    results.append(checks.check_content_extraction(engine, nodes))

    log("check: did the indexer change the mapping")
    results.append(
        checks.check_mapping_unchanged_by_indexer(
            mapping_before_indexer, checks.mapped_fields(engine.mapping())
        )
    )

    # Captured before the last two checks, which deliberately damage two documents.
    engine.refresh()
    data = snapshot.capture(
        engine,
        nodes,
        "community-batch-indexing",
        meta={"harnessVersion": VERSION, "images": stack.env()},
    )
    snapshot.write(data, DEFAULT_SNAPSHOT)
    log("index captured to %s" % DEFAULT_SNAPSHOT)

    log("check: can the Community indexer overwrite fresher live-indexed data")
    results.append(checks.check_live_indexing_takeover(client, engine, nodes))
    log("check: delete semantics")
    results.append(checks.check_delete_semantics(client, engine, nodes))
    results.append(checks.check_batch_residue(engine, nodes))
    results.append(checks.check_mapped_but_unwritten(engine, nodes))

    exit_code = _report(results)
    if not args.keep_up:
        log("leaving the stack running; stop it with: python3 -m harness down")
    return exit_code


# --- helpers ---


def _existing_fixtures():
    """Re-find the fixture nodes of a previous run, for snapshotting a live stack."""
    ids_file = stack.GENERATED / "fixture-ids.json"
    if ids_file.exists():
        return json.loads(ids_file.read_text(encoding="utf-8"))
    raise SystemExit(
        "no generated/fixture-ids.json: run `python3 -m harness run` first, or seed a "
        "repository with the same fixtures before snapshotting"
    )


def _write_fixture_ids(nodes):
    stack.GENERATED.mkdir(exist_ok=True)
    (stack.GENERATED / "fixture-ids.json").write_text(
        json.dumps({label: {"id": node["id"], "name": node["name"]} for label, node in nodes.items()},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _wait_for_mapping(engine, field, timeout=300):
    try:
        httpjson.wait_until(
            lambda: field in checks.mapped_fields(engine.mapping()),
            timeout=timeout,
            interval=5,
            description="the repository to map %s" % field,
        )
    except TimeoutError as error:
        log("warning: %s" % error)


def _wait_for_documents(engine, nodes, timeout=1200, exclude=()):
    labels = [label for label in contract.EXPECTATIONS if label not in exclude]

    def indexed():
        engine.refresh()
        missing = [label for label in labels if engine.document(nodes[label]["id"]) is None]
        if missing:
            log("waiting for %d of %d fixtures to be indexed" % (len(missing), len(labels)))
            return False
        return True

    httpjson.wait_until(
        indexed, timeout=timeout, interval=10, description="every fixture to be indexed"
    )
    log("all %d fixtures are in the index" % len(labels))


def _wait_for_text(engine, nodes, timeout=900, exclude=()):
    labels = [
        label
        for label, kinds in contract.EXPECTATIONS.items()
        if "text" in kinds and label not in exclude
    ]

    def extracted():
        engine.refresh()
        pending = [
            label
            for label in labels
            if names.CONTENT not in (engine.document(nodes[label]["id"]) or {})
        ]
        if pending:
            log("waiting for text extraction on %d of %d documents" % (len(pending), len(labels)))
            return False
        return True

    try:
        httpjson.wait_until(
            extracted, timeout=timeout, interval=10, description="text extraction to finish"
        )
        log("extracted text is present for all %d documents with content" % len(labels))
    except TimeoutError as error:
        log("warning: %s" % error)


def _wait_for_field(engine, doc_id, field, timeout=300):
    try:
        httpjson.wait_until(
            lambda: field in (checks.refreshed_document(engine, doc_id) or {}),
            timeout=timeout,
            interval=5,
            description="%s to appear on document %s" % (field, doc_id),
        )
    except TimeoutError as error:
        log("warning: %s" % error)


def _report(results):
    payload = {
        "harnessVersion": VERSION,
        "images": stack.env(),
        "checks": [result.as_dict() for result in results],
    }
    payload["failed"] = [result.key for result in results if not result.ok]
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print()
    print("Index parity report")
    print("=" * 78)
    for result in results:
        print("%-5s %-24s %s" % (result.status, result.key, result.question))
        print("      %s" % result.summary)
    print("=" * 78)
    if payload["failed"]:
        print("FAILED: %s" % ", ".join(payload["failed"]))
    else:
        print("All checks passed. Evidence in %s" % REPORT_PATH.name)
    print("Full evidence: %s" % REPORT_PATH)
    return 1 if payload["failed"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="harness", description=__doc__)
    parser.add_argument("--acs", default=acs.DEFAULT_BASE, help="repository base URL")
    parser.add_argument("--opensearch", default=search.DEFAULT_BASE, help="OpenSearch base URL")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="start the stack and run every check")
    run.add_argument(
        "--no-reset",
        action="store_true",
        help="do not tear the stack down first (the index must still be empty)",
    )
    run.add_argument("--keep-up", action="store_true", help=argparse.SUPPRESS)
    run.set_defaults(func=cmd_run)

    subparsers.add_parser("prepare", help="write generated/prefixes.json from the indexer image").set_defaults(
        func=cmd_prepare
    )
    subparsers.add_parser("up", help="start everything except the indexer").set_defaults(func=cmd_up)
    subparsers.add_parser("down", help="stop the stack and delete its volumes").set_defaults(
        func=cmd_down
    )

    capture = subparsers.add_parser("snapshot", help="capture the index of a running stack")
    capture.add_argument("--label", default="unlabelled", help="who wrote this index")
    capture.add_argument("--out", default=str(DEFAULT_SNAPSHOT))
    capture.set_defaults(func=cmd_snapshot)

    compare = subparsers.add_parser("diff", help="compare two captures")
    compare.add_argument("left")
    compare.add_argument("right")
    compare.add_argument(
        "--values",
        action="store_true",
        help="compare field values too; only meaningful when both captures indexed the same "
        "repository database",
    )
    compare.add_argument("--out", help="write the full diff here")
    compare.set_defaults(func=cmd_diff)

    # No subcommand means the whole thing.
    parser.set_defaults(func=cmd_run, no_reset=False, keep_up=False)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
