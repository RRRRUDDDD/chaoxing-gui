"""Check or synchronize release versions; pyproject.toml is never rewritten."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib


VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?")
ARTIFACT = re.compile(r"chaoxing-gui-tauri-(setup|portable)-(.+)-windows-x64\.(exe|zip)")


def read_text(path):
    return path.read_bytes().decode("utf-8-sig")


def replace_toml_version(text, version, *, package=None):
    sections = re.split(r"(?m)(?=^\[)", text)
    found = 0
    for index, section in enumerate(sections):
        if package is None:
            selected = section.splitlines()[:1] == ["[package]"]
        else:
            selected = section.startswith("[[package]]") and (
                tomllib.loads(section)["package"][0].get("name") == package
            )
        if selected:
            sections[index], count = re.subn(
                r'(?m)^([ \t]*version[ \t]*=[ \t]*)[\"\'][^\"\']*[\"\']([ \t]*(?:#.*)?)(\r?)$',
                lambda match: f'{match[1]}"{version}"{match[2]}{match[3]}',
                section,
            )
            if count != 1:
                raise ValueError("Expected exactly one application version in TOML section")
            found += 1
    if found != 1:
        raise ValueError("Expected exactly one application package in TOML")
    return "".join(sections)


def inspect(root, synchronize):
    canonical = tomllib.loads(read_text(root / "pyproject.toml"))["project"]["version"]
    if not isinstance(canonical, str) or VERSION.fullmatch(canonical) is None:
        raise ValueError("pyproject.toml project.version must be a safe SemVer release version")
    expected = []
    changes = {}

    for project in ("web", "desktop"):
        for filename in ("package.json", "package-lock.json"):
            relative = f"{project}/{filename}"
            path = root / relative
            data = json.loads(read_text(path))
            expected.append((relative, data["version"]))
            if filename == "package-lock.json":
                expected.append((relative + " packages['']", data["packages"][""]["version"]))
                data["packages"][""]["version"] = canonical
            data["version"] = canonical
            changes[path] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    relative = "desktop/src-tauri/tauri.conf.json"
    data = json.loads(read_text(root / relative))
    expected.append((relative, data["version"]))
    data["version"] = canonical
    changes[root / relative] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    relative = "desktop/src-tauri/Cargo.toml"
    cargo_text = read_text(root / relative)
    cargo_package = tomllib.loads(cargo_text)["package"]
    expected.append((relative, cargo_package["version"]))
    changes[root / relative] = replace_toml_version(cargo_text, canonical)

    relative = "desktop/src-tauri/Cargo.lock"
    lock_text = read_text(root / relative)
    packages = [p for p in tomllib.loads(lock_text)["package"] if p["name"] == cargo_package["name"]]
    if len(packages) != 1:
        raise ValueError(f"{relative}: expected one {cargo_package['name']} package")
    expected.append((relative, packages[0]["version"]))
    changes[root / relative] = replace_toml_version(lock_text, canonical, package=cargo_package["name"])

    if synchronize:
        # All inputs have been parsed and validated before changing any file.
        for path, content in changes.items():
            path.write_bytes(content.encode("utf-8"))
        expected = [(name, canonical) for name, _ in expected]
    errors = [f"{name}: {actual!r} != pyproject.toml {canonical!r}"
              for name, actual in expected if actual != canonical]
    return canonical, expected, errors


def check_tags(root, supplied):
    tags = list(supplied)
    if (root / ".git").exists():
        result = subprocess.run(["git", "-C", str(root), "tag", "--points-at", "HEAD"],
                                check=True, capture_output=True, text=True, timeout=15)
        tags.extend(tag for tag in result.stdout.splitlines() if tag.startswith("v"))
    return sorted(set(tags))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check only (the default)")
    mode.add_argument("--sync", action="store_true", help="copy the source version into metadata")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--tag", action="append", default=[], help="also validate this release tag")
    parser.add_argument("--artifacts", type=Path, help="validate all Tauri exe/zip artifact names here")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    args = parser.parse_args(argv)
    report = {"success": False, "version": None, "errors": []}
    try:
        root = args.root.resolve(strict=True)
        # Tag mismatch is also checked before sync so an invalid release cannot
        # partially update metadata while returning a failure.
        tags = check_tags(root, args.tag)
        source = tomllib.loads(read_text(root / "pyproject.toml"))["project"]["version"]
        for tag in tags:
            normalized = tag.removeprefix("refs/tags/").removeprefix("v")
            if normalized != source:
                raise ValueError(f"release tag {tag!r} does not match pyproject.toml {source!r}")
        version, entries, errors = inspect(root, args.sync)
        artifacts = []
        if args.artifacts is not None:
            directory = args.artifacts.resolve(strict=True)
            for path in sorted(directory.iterdir()):
                if path.is_file() and path.suffix.lower() in (".exe", ".zip"):
                    match = ARTIFACT.fullmatch(path.name)
                    if (match is None or match[2] != version or
                            match[3] != {"setup": "exe", "portable": "zip"}[match[1]]):
                        errors.append(f"artifact {path.name!r} does not match Tauri version {version}")
                    artifacts.append(path.name)
            if not artifacts:
                errors.append("artifact directory contains no Tauri exe/zip files")
        report.update(success=not errors, version=version, errors=errors,
                      metadata=[{"path": name, "version": value} for name, value in entries],
                      tags=tags, artifacts=artifacts, synchronized=args.sync)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report["errors"].append(str(error))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["success"]:
        print(f"Version {report['version']}: metadata, tags and selected artifacts agree")
    else:
        print("\n".join(report["errors"]), file=sys.stderr)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
