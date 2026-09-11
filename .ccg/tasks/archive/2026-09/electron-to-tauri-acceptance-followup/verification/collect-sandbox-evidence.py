"""Copy one completed Sandbox run into the task, preserving exact evidence bytes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    info = path.lstat()
    if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f"Refusing redirected evidence: {path}")
    return info


def walk(directory):
    regular(directory)
    for entry in sorted(directory.iterdir()):
        info = regular(entry)
        if stat.S_ISDIR(info.st_mode):
            if entry.name == "fixtures":
                raise ValueError("Guest export unexpectedly contains bulk fixtures")
            yield from walk(entry)
        elif stat.S_ISREG(info.st_mode):
            yield entry
        else:
            raise ValueError(f"Unsupported evidence file: {entry}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    args = parser.parse_args()
    if not re.fullmatch(r"run-[0-9a-f]{32}", args.run_id):
        raise ValueError("Expected exact Sandbox run ID")
    task = Path(__file__).resolve().parents[1]
    verification = task / "verification"
    repo = Path(subprocess.check_output(
        ["git", "-C", str(task), "rev-parse", "--show-toplevel"], text=True, timeout=15).strip())
    target = (repo / "desktop/src-tauri/target").resolve(strict=True)
    launch = json.loads((verification / f"sandbox-launch-{args.run_id}.json").read_text(encoding="utf-8-sig"))
    output = Path(launch["outputDirectory"])
    output.resolve(strict=True).relative_to(target)
    if output.name != "output" or output.parent.name != args.run_id:
        raise ValueError("Launch output is not the named dedicated run")
    kit = output.parent.parent
    if not re.fullmatch(r"acceptance-followup-kit-[0-9a-f]{32}", kit.name):
        raise ValueError("Unexpected kit path")
    for ancestor in (target, kit, output.parent, output):
        regular(ancestor)
    kit_id = kit.name.removeprefix("acceptance-followup-kit-")
    metadata_path = verification / f"sandbox-kit-{kit_id}.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    inputs = kit / "input"
    if inputs.resolve(strict=True) != Path(metadata["inputDirectory"]).resolve(strict=True):
        raise ValueError("Input metadata path mismatch")
    regular(inputs)
    regular(inputs / "input-manifest.json")
    manifest_bytes = (inputs / "input-manifest.json").read_bytes()
    if digest(manifest_bytes) != metadata["inputManifestSha256"]:
        raise ValueError("Input manifest changed")
    input_manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    input_entries = {entry["path"]: entry for entry in input_manifest["files"]}
    result_path = output / "result.json"
    regular(result_path)
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    if (not result.get("endedAt") or result.get("sourceCommit") != launch["sourceCommit"]
            or result.get("sourceCommit") != input_manifest["sourceCommit"]
            or not result.get("identity", "").endswith("\\WDAGUtilityAccount")
            or result.get("computerModel") != "Virtual Machine"):
        raise ValueError("No completed matching actual Sandbox result")
    destination = verification / "sandbox-runs" / args.run_id
    if destination.exists():
        raise ValueError("Collected evidence exists; refusing replacement")
    destination.mkdir(parents=True, exist_ok=False)
    records = []

    def copy(source, relative):
        before = regular(source)
        if before.st_size > 20 * 1024 * 1024:
            raise ValueError("Unexpectedly large evidence file")
        data = source.read_bytes()
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Evidence changed during collection")
        copied = destination / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        with copied.open("xb") as stream:
            stream.write(data)
        if digest(copied.read_bytes()) != digest(data):
            raise ValueError("Copied evidence hash mismatch")
        records.append({"source": str(source), "path": relative.as_posix(), "bytes": len(data), "sha256": digest(data)})

    for source in walk(output):
        if source.suffix not in (".json", ".txt", ".log", ".md", ".png"):
            raise ValueError(f"Unexpected guest evidence extension: {source}")
        copy(source, Path("output") / source.relative_to(output))
    config = Path(launch["configPath"])
    if config.resolve(strict=True) != (output.parent / "acceptance.wsb").resolve(strict=True):
        raise ValueError("Sandbox config path mismatch")
    copy(config, Path("acceptance.wsb"))
    for source in sorted(inputs.iterdir()):
        regular(source)
        if source.is_file() and source.suffix in (".ps1", ".mjs", ".py"):
            entry = input_entries.get(source.name)
            if not entry or digest(source.read_bytes()) != entry["sha256"]:
                raise ValueError("Guest helper hash mismatch")
            copy(source, Path("guest-inputs") / source.name)
    for relative in ("acceptance-mode.json", "native/preparation.json", "native/p3-installation-native-partial.mjs"):
        if relative not in input_entries:
            continue
        source = inputs / relative
        regular(source.parent)
        regular(source)
        if digest(source.read_bytes()) != input_entries[relative]["sha256"]:
            raise ValueError("Supplemental input hash mismatch")
        copy(source, Path("guest-inputs") / relative)
    collected = {
        "schemaVersion": 1, "collectedAt": datetime.now(timezone.utc).isoformat(),
        "runId": args.run_id, "sourceCommit": result["sourceCommit"], "guestSuccess": result["success"],
        "scope": result.get("scope", "standard-release-acceptance"),
        "fullAcceptancePassed": result.get("fullAcceptancePassed", result["success"]),
        "remoteCI": False, "productExecutedOnHost": False,
        "inputManifest": f"verification/sandbox-input-manifest-{kit_id}.json",
        "inputManifestSha256": metadata["inputManifestSha256"], "files": records,
    }
    with (destination / "collection.json").open("x", encoding="utf-8") as stream:
        json.dump(collected, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in collected.items() if key != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
