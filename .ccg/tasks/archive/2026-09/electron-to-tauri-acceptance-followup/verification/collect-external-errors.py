"""Capture bounded, redacted API-error evidence without rerunning external models."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


TASK = Path(__file__).resolve().parents[1]
SESSION_ROOT = Path("C:/Users/RUD/.claude/projects/E--Downloads-45-chaoxing-gui")
STAGES = ("followup-analysis", "p0p1p2-review", "p3-review")
SESSION_ID = re.compile(
    r"Session[- ]ID\s*[:=]\s*([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})(?![0-9a-f-])",
    re.IGNORECASE,
)
SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(
        r"\b(?:api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|client[-_ ]?secret|"
        r"token|password|authorization)\s*[\"']?\s*[:=]\s*[\"']?[^\s\"'`,;}\]]+",
        re.IGNORECASE,
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{40,}(?![A-Za-z0-9])"),
)


def redact(value):
    """Apply only to allowlisted error fields; never serialize arbitrary records."""
    if not isinstance(value, str):
        return None
    result = value
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result[:2000] + (" [TRUNCATED]" if len(result) > 2000 else "")


def read_stable(path):
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("Source changed during collection")
    return data, {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def utc_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def api_errors(session_id, accessed):
    """Read only the exact file named by a completed lane's stderr Session-ID."""
    path = SESSION_ROOT / (session_id + ".jsonl")
    result = {"sessionId": session_id, "file": path.as_posix(), "errors": [], "issues": []}
    if path.resolve().parent != SESSION_ROOT.resolve() or path.is_symlink():
        result["issues"].append("Session path is redirected; file was not read")
        return result
    digest = hashlib.sha256()
    try:
        before = path.stat()
        with path.open("rb") as stream:
            accessed.append({"sessionId": session_id, "file": path.as_posix()})
            for line_number, raw in enumerate(stream, 1):
                digest.update(raw)
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw.decode("utf-8-sig"))
                except (UnicodeError, json.JSONDecodeError):
                    result["issues"].append(f"Malformed JSON at line {line_number}; content omitted")
                    continue
                if not isinstance(record, dict) or record.get("isApiErrorMessage") is not True:
                    continue
                if record.get("sessionId") != session_id:
                    result["issues"].append(f"Session-ID mismatch at line {line_number}; content omitted")
                    continue
                message = record.get("message")
                blocks = message.get("content", []) if isinstance(message, dict) else []
                texts = [block["text"] for block in blocks if isinstance(block, dict)
                         and block.get("type") == "text" and isinstance(block.get("text"), str)]
                original = "\n".join(texts)
                status = record.get("apiErrorStatus")
                status = int(status) if str(status).isdigit() and 100 <= int(status) <= 599 else None
                error_type = record.get("error")
                safe_message, safe_type = redact(original), redact(error_type)
                result["errors"].append({
                    "line": line_number,
                    "timestamp": redact(record.get("timestamp")),
                    "isApiErrorMessage": True,
                    "apiErrorStatus": status,
                    "apiErrorIsTransient": record.get("apiErrorIsTransient") is True,
                    "errorType": safe_type,
                    "message": safe_message,
                    "redactionApplied": safe_message != original or safe_type != error_type,
                })
        after = path.stat()
        result["sha256"] = digest.hexdigest()
        result["bytes"] = after.st_size
        result["sourceStable"] = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
        if not result["sourceStable"]:
            result["issues"].append("Session file changed while read; snapshot may be incomplete")
    except OSError as exc:
        result["issues"].append(f"Exact session file unavailable: {type(exc).__name__}")
    return result


def collect_lane(stage, lane, accessed):
    name = f"{stage}-{lane}"
    result = {"name": name, "stage": stage, "lane": lane, "availability": "unavailable",
              "passed": False, "reportBodyPresent": None, "sources": {}, "sessions": [], "issues": []}
    prefix = TASK / "research" / name
    try:
        raw, metadata = read_stable(prefix.with_suffix(".result.json"))
        invocation = json.loads(raw.decode("utf-8-sig"))
        if (not isinstance(invocation, dict) or invocation.get("stage") != stage
                or invocation.get("lane") != lane or type(invocation.get("exitCode")) is not int
                or type(invocation.get("timedOut")) is not bool or not invocation.get("endedAt")):
            raise ValueError("No valid completed invocation result")
        duration = (utc_time(invocation["endedAt"]) - utc_time(invocation["startedAt"])).total_seconds()
        if duration < 0:
            raise ValueError("Invalid invocation duration")
        result["sources"]["result"] = {"file": f"research/{name}.result.json", **metadata}
        for suffix in ("stdout.md", "stderr.log"):
            contents, metadata = read_stable(prefix.with_suffix("." + suffix))
            result["sources"][suffix.split(".")[0]] = {"file": f"research/{name}.{suffix}", **metadata}
            if suffix == "stdout.md":
                stdout = contents.decode("utf-8-sig")
            else:
                stderr = contents.decode("utf-8-sig")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result["issues"].append(f"Completed evidence unavailable: {type(exc).__name__}; active files were not changed")
        return result

    for key in ("startedAt", "endedAt", "timeoutSeconds", "timedOut", "exitCode"):
        result[key] = invocation.get(key)
    result.update({"availability": "complete", "durationSeconds": round(duration, 3),
                   "stdoutNonWhitespace": bool(stdout.strip()),
                   "reportBodyPresent": None if stdout.strip() else False,
                   "wrapperInvocationSucceeded": invocation.get("invocationSucceeded"),
                   "wrapperReviewPassed": invocation.get("reviewPassed")})
    ids = list(dict.fromkeys(match.lower() for match in SESSION_ID.findall(stderr)))
    result["sessionIdsFromStderr"] = ids
    for session_id in ids:
        result["sessions"].append(api_errors(session_id, accessed))
    if not ids:
        result["issues"].append("No valid Session-ID in stderr; no session files accessed")
    errors = [error for session in result["sessions"] for error in session["errors"]]
    result["apiStatuses"] = sorted({error["apiErrorStatus"] for error in errors if error["apiErrorStatus"] is not None})
    failed = invocation["exitCode"] != 0 or invocation["timedOut"] or not stdout.strip() or bool(errors)
    result["passed"] = False if failed else None
    result["assessment"] = ("No passing report: invocation failed, API error, timeout, or blank stdout"
                            if failed else "Nonempty output requires lead interpretation; no pass inferred")
    return result


def markdown(evidence):
    rows = ["# External invocation evidence", "", f"Collected at `{evidence['collectedAt']}`.", "",
            "All values below come from completed invocation files and structured API-error records. "
            "A failed invocation or blank stdout is `passed=false`; this collector never grants a review pass.", "",
            "| Lane | Exit | Timed out | Duration (s) | Stdout bytes | Report body | API status | Passed |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for lane in evidence["lanes"]:
        value = lambda key: json.dumps(lane.get(key), ensure_ascii=False)
        rows.append(f"| {lane['name']} | {value('exitCode')} | {value('timedOut')} | {value('durationSeconds')} "
                    f"| {lane['sources'].get('stdout', {}).get('bytes', 'unavailable')} "
                    f"| {value('reportBodyPresent')} | {value('apiStatuses')} | {value('passed')} |")
    rows += ["", "## Actual API errors", ""]
    for lane in evidence["lanes"]:
        for session in lane["sessions"]:
            for error in session["errors"]:
                message = (error["message"] or "No error text recorded").replace("`", "'").replace("\n", " ")
                rows.append(f"- `{lane['name']}`: `{error['timestamp']}`, JSONL line {error['line']}, "
                            f"status `{error['apiErrorStatus']}`, type `{error['errorType']}`: `{message}`")
    rows += ["", "## Exact session-file access", "",
             "Session IDs were extracted from each completed lane's `stderr.log`. "
             "Only these exact files under `C:/Users/RUD/.claude/projects/E--Downloads-45-chaoxing-gui/` "
             "were opened; no session-directory enumeration was performed.", ""]
    rows += [f"- `{item['sessionId']}.jsonl`" for item in evidence["accessedSessionFiles"]]
    rows += ["", "## Collection boundaries and verification", "",
             "- Source result/stdout/stderr and accessed session SHA256 values are recorded in `../verification/external-errors.json`.",
             "- Output contains allowlisted API-error fields only. User prompts, tool inputs, general messages, and full sessions are omitted; potential credentials are redacted.",
             "- Incomplete or changing sources are recorded as unavailable; this run did not alter any invocation files.",
             "- The collector uses exclusive creation and refuses to overwrite either finished output file.",
             "- The collector was checked by in-memory Python compilation before this capture. No model, network, release host, installer, or business AppData access was used.",
             "- Exit 0 and nonempty stdout would still require lead review of the report. These records establish invocation failure, not a substantive code-review finding."]
    issues = [f"{lane['name']}: {issue}" for lane in evidence["lanes"] for issue in lane["issues"]]
    issues += [f"{lane['name']}/{session['sessionId']}: {issue}" for lane in evidence["lanes"]
               for session in lane["sessions"] for issue in session["issues"]]
    rows += ["", "Collection issues: " + ("; ".join(issues) if issues else "none."), ""]
    return "\n".join(rows)


def main():
    output = TASK / "verification" / "external-errors.json"
    report = TASK / "research" / "external-evidence.md"
    if output.exists() or report.exists():
        raise SystemExit("Finished external-error evidence exists; refusing to overwrite")
    accessed = []
    evidence = {"schemaVersion": 1, "collectedAt": datetime.now(timezone.utc).isoformat(),
                "sessionRoot": SESSION_ROOT.as_posix(), "collector": "verification/collect-external-errors.py",
                "passingReviewEstablished": False,
                "lanes": [collect_lane(stage, lane, accessed) for stage in STAGES for lane in ("a", "b")],
                "accessedSessionFiles": accessed}
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    with report.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(markdown(evidence))
    print(json.dumps({"output": str(output), "report": str(report), "lanes": len(evidence["lanes"]),
                      "sessionsAccessed": len(accessed), "passingReviewEstablished": False}))


if __name__ == "__main__":
    main()
