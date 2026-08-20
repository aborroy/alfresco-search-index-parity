"""Driving the Compose stack, and reading the indexer's own configuration out of its image."""

import io
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / "generated"
SNAPSHOTS = ROOT / "snapshots"

INDEXER_SERVICE = "batch-indexer"
CORE_SERVICES = ["postgres", "transform-core-aio", "alfresco", "opensearch"]

# Where the batch indexing image keeps its Spring Boot application and, nested inside it, the
# reindexing module that owns the namespace prefix map.
APP_JAR_IN_IMAGE = "/opt/app.jar"
REINDEXING_JAR_MARKER = "alfresco-elasticsearch-reindexing"
PREFIXES_RESOURCE = "reindex.prefixes-file.json"


def env():
    """The pinned tags and credentials from .env."""
    values = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def indexer_image():
    return "alfresco/alfresco-elasticsearch-batch-indexing:" + env()["BATCH_INDEXER_TAG"]


# --- Compose ---


def compose(*args, capture=False, check=True):
    command = ["docker", "compose", *args]
    if capture:
        result = subprocess.run(
            command, cwd=ROOT, check=check, capture_output=True, text=True
        )
        return result.stdout
    subprocess.run(command, cwd=ROOT, check=check)
    return None


def up_core():
    compose("up", "-d", *CORE_SERVICES)


def up_indexer():
    compose("--profile", "indexer", "up", "-d", INDEXER_SERVICE)


def restart_indexer():
    """Recreate the indexer so it re-reads the mounted prefix map."""
    compose("--profile", "indexer", "rm", "-sf", INDEXER_SERVICE)
    up_indexer()


def down():
    compose("--profile", "indexer", "down", "-v")


def running_services():
    """Compose has emitted both a JSON array and one object per line over its versions."""
    output = (compose("ps", "--all", "--format", "json", capture=True) or "").strip()
    if not output:
        return []
    try:
        parsed = json.loads(output)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        return [json.loads(line) for line in output.splitlines() if line.strip()]


def indexer_container_exists():
    return any(item.get("Service") == INDEXER_SERVICE for item in running_services())


def logs(service=INDEXER_SERVICE, tail="all"):
    return compose("logs", "--no-log-prefix", "--tail", str(tail), service, capture=True) or ""


# --- The indexer's namespace prefix map ---


def stock_prefixes():
    """Read the prefix map the batch indexing image ships.

    Taken from the image rather than copied into this repository, so it always describes the
    tag pinned in .env and never drifts from it.
    """
    cached = GENERATED / "prefixes-stock.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))["prefixUriMap"]

    GENERATED.mkdir(exist_ok=True)
    container = subprocess.run(
        ["docker", "create", indexer_image()],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    handle, jar_path = tempfile.mkstemp(suffix=".jar")
    os.close(handle)
    try:
        subprocess.run(
            ["docker", "cp", "%s:%s" % (container, APP_JAR_IN_IMAGE), jar_path],
            check=True,
            capture_output=True,
        )
        content = _read_nested_resource(jar_path)
    finally:
        subprocess.run(["docker", "rm", container], check=False, capture_output=True)
        os.remove(jar_path)

    cached.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return content["prefixUriMap"]


def _read_nested_resource(jar_path):
    with zipfile.ZipFile(jar_path) as outer:
        inner_names = [
            name
            for name in outer.namelist()
            if REINDEXING_JAR_MARKER in name and name.endswith(".jar")
        ]
        if not inner_names:
            raise RuntimeError("no %s jar inside %s" % (REINDEXING_JAR_MARKER, APP_JAR_IN_IMAGE))
        with zipfile.ZipFile(io.BytesIO(outer.read(inner_names[0]))) as inner:
            return json.loads(inner.read(PREFIXES_RESOURCE).decode("utf-8"))


def write_prefixes(prefix_uri_map, extra=None):
    """Write the map the container reads at generated/prefixes.json."""
    merged = dict(prefix_uri_map)
    if extra:
        merged.update(extra)
    GENERATED.mkdir(exist_ok=True)
    target = GENERATED / "prefixes.json"
    target.write_text(
        json.dumps({"prefixUriMap": merged}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target
