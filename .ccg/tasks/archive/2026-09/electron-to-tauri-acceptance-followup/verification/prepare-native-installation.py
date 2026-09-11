#!/usr/bin/env python3
"""Prepare a guest-only supplemental installation module; never run a product.

The exact committed installation source is pinned below. Only its smokeLayout
region is replaced; installer, manifest, registry, retention, and cleanup code
outside that region remains byte-for-byte identical. The generated module must
be copied beside the original scripts in the *guest* source tree and launched
through sandbox-guest.ps1 after that wrapper verifies the real Sandbox identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


SOURCE_SHA256 = "ecabd8c7330f390e208ea6c5c2aecf5eb7f14e68ba09d19e430170865b721b1c"
SOURCE_COMMIT = "12bae2286bec71bf184f19c8be67e6431199e134"
OUTPUT_NAME = "p3-installation-native-partial.mjs"

OLD_SMOKE_LAYOUT = r"""  async function smokeLayout(name, directory) {
    const host = path.join(directory, 'chaoxing-gui-tauri.exe');
    const smokeEvidence = path.join(evidence, `${name}-host`);
    const args = ['-HostPath', host, '-BackendDirectory', path.join(directory, 'backend'), '-Configuration', 'Release', '-Scenario', 'Frozen',
      '-UsePackagedLayout', '-NestedJob', '-EvidenceDirectory', smokeEvidence, '-TimeoutSeconds', String(Math.max(1, Math.min(120, Math.floor(options.timeoutSeconds / 3))))];
    if (options.disposableWindowsUser) args.push('-DisposableWindowsUser');
    const invocation = { name, evidenceDirectory: smokeEvidence };
    childProfileInvocations.push(invocation);
    await withCleanup(invocation, async () => {
      await runScript(`${name}-real-host`, 'smoke-tauri.ps1', args, { profileInvocation: invocation });
      const reports = await readdir(smokeEvidence);
      assert.equal(reports.length, 1, 'Each layout smoke must produce exactly one fresh report');
      const reportPath = path.join(smokeEvidence, reports[0], 'result.json');
      const report = await json(reportPath);
      assert.equal(report.success, true, `${name} host smoke failed`);
      assert.equal(report.selection.configuration, 'Release');
      assert.equal(report.selection.usePackagedLayout, true);
      assert.ok(report.checks.some((check) => check.name === 'frozen-force-host-forced-no-residue'), 'Installed layout must complete forced-host nested Job cleanup');
      record(`${name}-real-release-layout`, { directory, reportPath });
    }, (value) => cleanupChildProfileInvocation(value, context));
  }
"""

PREFIX_ANCHOR = r"""  const runInline = (name, script, request) => runChecked(name, context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded(script)],
    { env: { ...process.env, P3_INSTALLATION_REQUEST: JSON.stringify(request) } });

"""
SUFFIX_ANCHOR = "\n  try {\n    options = parseInstallationArguments(argv);\n"

# Scope metadata executes before the original try/preflight so even an early
# refusal is labelled partial. It is still contained in the one replaced span.
NATIVE_SMOKE_LAYOUT = r"""  // Supplemental guest test copy; the committed CDP acceptance is unchanged.
  result.scope = 'native-installation-partial';
  result.acceptanceScope = 'supplemental-native-installation';
  result.fullAcceptancePassed = false;
  result.formalReleaseSmokePassed = false;
  result.nativePreparation = {
    sourceInstallationSha256: 'ecabd8c7330f390e208ea6c5c2aecf5eb7f14e68ba09d19e430170865b721b1c',
    replacedRegion: 'installationMain.smokeLayout',
    requiredLauncher: 'sandbox-guest.ps1 actual WDAGUtilityAccount / Virtual Machine guard',
  };
  result.limitations.push(
    'success applies only to this supplemental native installation sequence; fullAcceptancePassed remains false',
    'No frontend rendering, Tauri IPC/backend Ready, business, cancellation, HTTP 400/404, or CDP acceptance is performed',
    'No remote CI or formal P3 release acceptance pass; the original CDP timeout evidence remains a failure',
    'Native process presence is not proof of frontend usability or backend API readiness',
  );

  async function smokeLayout(name, directory) {
    assert.ok(['portable', 'installed'].includes(name), 'Unexpected native layout');
    assertReleasePermission({ configuration: 'Release', disposableWindowsUser: options.disposableWindowsUser });
    const host = path.join(directory, 'chaoxing-gui-tauri.exe');
    const backend = path.join(directory, 'backend', 'chaoxing-backend.exe');
    for (const executable of [host, backend]) {
      assert.ok(await checkNoLinks(executable), 'Native layout executable must exist without linked ancestors');
      assert.ok((await lstat(executable)).isFile(), 'Native layout executable must be a regular file');
    }
    const configurationPath = path.join(repo, 'desktop/src-tauri/tauri.conf.json');
    assert.ok(await checkNoLinks(configurationPath), 'Window configuration must exist without links');
    const configuration = await json(configurationPath);
    assert.ok(Array.isArray(configuration.app?.windows), 'Missing configured application windows');
    const mainWindows = configuration.app.windows.filter((entry) => (entry.label ?? 'main') === 'main');
    assert.equal(mainWindows.length, 1, 'Exactly one configured main window is required');
    const windowTitle = mainWindows[0].title;
    assert.ok(typeof windowTitle === 'string' && windowTitle.length > 0 && windowTitle.length < 1024
      && !/[\x00-\x1f]/.test(windowTitle), 'A valid exact main-window title is required');
    const layoutEvidence = path.join(evidence, `${name}-native-host`);
    await mkdir(layoutEvidence);
    const timeout = Math.max(1000, Math.min(120000, Math.floor(options.timeoutSeconds * 1000 / 3)));
    const shutdownTimeout = Math.min(timeout, 20000);
    const sameIdentity = (left, right) => Boolean(left && right && left.pid === right.pid
      && left.createdAtFileTime === right.createdAtFileTime && canonical(left.executable) === canonical(right.executable));
    const fail = (message) => { const error = new Error(message); error.fatal = true; throw error; };

    // This diagnostic queries only captured Job members and retains a process
    // handle while checking its exact creation time around CIM/listener reads.
    // It never reads process environments, unrelated PIDs, or account data.
    const diagnosticScript = String.raw`
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p3Expected = @($env:P3_NATIVE_OWNED_IDENTITIES | ConvertFrom-Json)
$p3Rows = [Collections.Generic.List[object]]::new()
foreach ($p3Identity in $p3Expected) {
  if (-not $p3Identity.inOuterJob -or -not $p3Identity.alive -or [uint32]$p3Identity.pid -eq 0 -or [string]$p3Identity.createdAtFileTime -notmatch '^\d+$') { throw 'Invalid captured identity' }
  $p3Process = $null
  try {
    $p3Process = [Diagnostics.Process]::GetProcessById([int]$p3Identity.pid)
    [void]$p3Process.Handle
    $p3Created = $p3Process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString([Globalization.CultureInfo]::InvariantCulture)
    if ($p3Process.HasExited -or $p3Created -cne [string]$p3Identity.createdAtFileTime) { continue }
    $p3Cim = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [uint32]$p3Identity.pid) -ErrorAction Stop
    if ($null -eq $p3Cim -or [uint32]$p3Cim.ProcessId -ne [uint32]$p3Identity.pid -or -not $p3Cim.ExecutablePath -or
      [IO.Path]::GetFullPath([string]$p3Cim.ExecutablePath) -ine [IO.Path]::GetFullPath([string]$p3Identity.executable)) { continue }
    $p3Listeners = @(Get-NetTCPConnection -OwningProcess ([uint32]$p3Identity.pid) -State Listen -ErrorAction SilentlyContinue |
      Where-Object { $_.OwningProcess -eq [uint32]$p3Identity.pid -and $_.LocalAddress -in @('127.0.0.1','::1') } |
      ForEach-Object { @{pid=[uint32]$_.OwningProcess; address=[string]$_.LocalAddress; port=[uint16]$_.LocalPort} })
    if ($p3Process.HasExited -or $p3Process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString([Globalization.CultureInfo]::InvariantCulture) -cne [string]$p3Identity.createdAtFileTime) { continue }
    $p3Rows.Add(@{pid=[uint32]$p3Identity.pid; createdAtFileTime=[string]$p3Identity.createdAtFileTime;
      executable=[string]$p3Cim.ExecutablePath; commandLine=[string]$p3Cim.CommandLine; loopbackListeners=$p3Listeners})
  } catch {
    # Races with an already exiting owned process are diagnostic omissions,
    # never evidence that the native lifecycle assertion passed.
  } finally { if ($null -ne $p3Process) { $p3Process.Dispose() } }
}
@{processes=@($p3Rows.ToArray()); environmentRead=$false; unrelatedProcessesRead=$false} | ConvertTo-Json -Depth 8 -Compress
`;

    async function nativeScenario(scenario, args, action) {
      const scenarioName = `${name}-native-${scenario}`;
      const invocationEvidence = path.join(layoutEvidence, scenario);
      await mkdir(invocationEvidence);
      const scenarioEvidence = await mkdtemp(path.join(invocationEvidence, 'smoke-'));
      const profileRunId = randomUUID();
      const roots = releaseProfileRoots(context);
      const owner = new NativeSupervisor(context.powerShell, path.join(scenarioEvidence, 'process'));
      const item = { name: scenarioName, acceptanceScope: result.acceptanceScope, executable: host, args,
        expectedBackend: backend, configuration: 'Release', usePackagedLayout: true,
        profileRunId, evidenceDirectory: scenarioEvidence, windowTitle,
        productionPathsOverridden: false, outerJobAssignedBeforeResume: false, completed: false };
      const invocation = { name: scenarioName, evidenceDirectory: invocationEvidence, process: item };
      result.processes.push(item);
      childProfileInvocations.push(invocation);
      const inspect = async (requireAlive = false) => {
        const snapshot = await owner.snapshot();
        item.lastSnapshot = snapshot;
        if (!snapshot.outerJobHandleRetained || !sameIdentity(snapshot.host, item.identity)
          || !snapshot.host.inOuterJob || snapshot.active.some((identity) => !identity.inOuterJob)
          || !snapshot.observed.some((identity) => sameIdentity(identity, item.identity))) {
          fail('Native observation must retain the outer Job and exact captured host identity');
        }
        if (requireAlive && !snapshot.host.alive) fail(`Captured host exited before native startup/shutdown action (exit ${snapshot.host.exitCode})`);
        return snapshot;
      };
      await withCleanup(item, async () => {
        await assertOwnedOrAbsent(roots, profileRunId, context.sid);
        assert.ok(await checkNoLinks(scenarioEvidence), 'Fresh native evidence must not contain linked ancestors');
        await writeFile(path.join(scenarioEvidence, 'profile-ownership.json'), JSON.stringify({
          schemaVersion: 1, configuration: 'Release', runId: profileRunId, sid: context.sid,
          evidenceDirectory: path.resolve(scenarioEvidence), roots,
        }), { flag: 'wx' });
        // Publish ownership before any claim/start so the unchanged parent
        // cleanup can retry using the same unique child run ID and captured Job.
        await claimProfileRoots(roots, profileRunId, context.sid);
        item.profileOwnership = { runId: profileRunId, sid: context.sid, roots };
        item.identity = await owner.start({ executable: host, args, cwd: directory,
          env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
        assert.equal(canonical(item.identity.executable), canonical(host), 'Unexpected native host image');
        assert.equal(item.identity.inOuterJob, true, 'Native host was not assigned to the outer Job');
        item.outerJobAssignedBeforeResume = true;
        await action({ owner, item, inspect });
      }, async () => {
        try { item.cleanup = await owner.dispose(); }
        catch (error) { item.cleanup = owner.cleanup || { verified: false }; throw error; }
        assert.equal(item.cleanup.verified, true, 'Native profile cleanup requires verified captured process cleanup');
        assert.deepEqual(item.cleanup.remaining, [], 'Native profile cleanup requires an empty captured Job');
        assert.ok(Array.isArray(item.cleanup.observed) && item.cleanup.observed.every((identity) => !identity.alive)
          && item.cleanup.observed.some((identity) => sameIdentity(identity, item.identity)),
        'Native profile cleanup requires all captured identities dead, including the host');
        // The existing helper rechecks SID/run ID, real known-folder paths,
        // marker ownership and links before removing only this invocation's roots.
        await cleanupChildProfileInvocation(invocation, context);
        assert.equal(item.cleanup.fallbackUsed, false, 'Forced supervisor cleanup cannot satisfy native lifecycle acceptance');
      });
      item.completed = true;
      return item;
    }

    const buildProbe = await nativeScenario('build-profile', ['--check-debug-build'], async ({ item, inspect }) => {
      item.shutdown = await until(async () => {
        const snapshot = await inspect();
        if (!snapshot.host.alive && snapshot.host.exitCode !== 4) fail(`Native Release build probe expected exit 4, received ${snapshot.host.exitCode}`);
        return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive) && snapshot;
      }, `${name} native Release build probe`, Math.min(timeout, 10000), 100);
      assert.equal(item.shutdown.host.exitCode, 4);
    });
    record(`${name}-native-compiled-release-profile`, { exitCode: buildProbe.shutdown.host.exitCode,
      capturedHost: buildProbe.identity, fallbackUsed: buildProbe.cleanup.fallbackUsed });

    const lifecycle = [];
    for (const force of [false, true]) {
      const scenario = force ? 'force-host' : 'normal-close';
      const item = await nativeScenario(scenario, [], async ({ owner, item, inspect }) => {
        const startup = await until(async () => {
          const snapshot = await inspect(true);
          const backends = snapshot.active.filter((identity) => canonical(identity.executable) === canonical(backend));
          const webviews = snapshot.active.filter((identity) => path.win32.basename(identity.executable).toLowerCase() === 'msedgewebview2.exe');
          return backends.length > 0 && webviews.length > 0 && { snapshot, backends, webviews };
        }, `${name} captured frozen backend and WebView2 process presence`, timeout, 200);
        item.startup = { ...startup, assertion: 'process presence only; no IPC/API/UI readiness claim' };
        try {
          const { stdout } = await exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded(diagnosticScript)], {
            windowsHide: true, timeout: 20000, maxBuffer: 1024 * 1024,
            env: { ...sanitizedEnvironment(process.env, context, { configuration: 'Release' }),
              P3_NATIVE_OWNED_IDENTITIES: JSON.stringify(startup.snapshot.active) },
          });
          const diagnostic = JSON.parse(stdout);
          const afterDiagnostic = await inspect(true);
          assert.ok(Array.isArray(diagnostic.processes), 'Invalid owned-process diagnostic');
          const retained = diagnostic.processes.filter((identity) => startup.snapshot.active.some((captured) => sameIdentity(captured, identity))
            && afterDiagnostic.active.some((captured) => sameIdentity(captured, identity)));
          for (const identity of retained) {
            assert.ok(Array.isArray(identity.loopbackListeners) && identity.loopbackListeners.every((listener) => listener.pid === identity.pid
              && ['127.0.0.1', '::1'].includes(listener.address)), 'Unexpected native diagnostic listener owner/address');
          }
          item.diagnostics = { collected: true, optional: true, processes: retained, noCDPRequests: true,
            captureRule: 'same PID, exact creation FILETIME and executable in both captured outer Job snapshots' };
        } catch (error) {
          item.diagnostics = { collected: false, optional: true, error: error.message, noCDPRequests: true };
        }
        const before = await inspect(true);
        assert.ok(before.active.some((identity) => canonical(identity.executable) === canonical(backend)), 'Frozen backend exited before native shutdown');
        assert.ok(before.active.some((identity) => path.win32.basename(identity.executable).toLowerCase() === 'msedgewebview2.exe'), 'WebView2 exited before native shutdown');
        let closedWindows = null;
        if (force) assert.equal(await owner.command('kill-host'), true);
        else {
          closedWindows = await until(async () => {
            await inspect(true);
            const count = await owner.command('close-window', { title: windowTitle });
            if (!Number.isInteger(count) || count < 0 || count > 1) fail('Normal close must target exactly one captured PID/title main window');
            return count === 1 && count;
          }, `${name} exact captured main-window WM_CLOSE`, shutdownTimeout, 100);
          assert.equal(closedWindows, 1);
        }
        const after = await until(async () => {
          const snapshot = await inspect();
          return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive) && snapshot;
        }, `${name} ${scenario} captured tree exit while outer Job remains open`, shutdownTimeout, 100);
        assert.equal(after.outerJobHandleRetained, true);
        assert.equal(after.host.exitCode, force ? 197 : 0, 'Unexpected native host shutdown code');
        item.shutdown = { mode: force ? 'terminate retained host handle' : 'WM_CLOSE exact captured PID and configured title',
          closedWindows, before, after, outerJobStillOpenAtObservation: true, noResidueBeforeFallback: true };
      });
      lifecycle.push(item);
      record(`${name}-native-${scenario}-no-residue`, { capturedHost: item.identity,
        observed: item.shutdown.after.observed, outerJobStillOpenAtObservation: true,
        closedWindows: item.shutdown.closedWindows, cleanupVerified: item.cleanup.verified, fallbackUsed: item.cleanup.fallbackUsed });
    }
    assert.equal(lifecycle.length, 2);
    assert.ok(lifecycle.every((item) => item.completed && item.cleanup.verified && item.cleanup.fallbackUsed === false));
    record(`${name}-native-release-layout`, { directory, host, expectedBackend: backend, windowTitle,
      evidenceDirectory: layoutEvidence, scenarios: lifecycle.map((item) => item.name),
      acceptanceScope: result.acceptanceScope, fullAcceptancePassed: false, formalReleaseSmokePassed: false });
  }
"""


def transform(source_bytes: bytes) -> tuple[bytes, dict[str, object]]:
    """Pure transformation, suitable for parser-only checks without any writes."""
    old = OLD_SMOKE_LAYOUT.encode("utf-8")
    header = b"\n  async function smokeLayout(name, directory) {\n"
    if source_bytes.count(header) != 1 or source_bytes.count(old) != 1:
        raise ValueError("Expected exactly one unchanged async smokeLayout region")
    start = source_bytes.index(old)
    end = start + len(old)
    if not source_bytes[:start].endswith(PREFIX_ANCHOR.encode("utf-8")):
        raise ValueError("smokeLayout prefix anchor does not match")
    if not source_bytes[end:].startswith(SUFFIX_ANCHOR.encode("utf-8")):
        raise ValueError("smokeLayout suffix anchor does not match")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if source_sha256 != SOURCE_SHA256:
        raise ValueError("Installation source differs from the pinned committed bytes")
    replacement = NATIVE_SMOKE_LAYOUT.encode("utf-8")
    generated = source_bytes[:start] + replacement + source_bytes[end:]
    if generated[:start] != source_bytes[:start] or generated[start + len(replacement):] != source_bytes[end:]:
        raise AssertionError("Transformation changed bytes outside smokeLayout")
    return generated, {
        "sourceCommit": SOURCE_COMMIT,
        "sourceSha256": source_sha256,
        "generatedSha256": hashlib.sha256(generated).hexdigest(),
        "replacedRegion": "installationMain.smokeLayout",
        "sourceRegionStartByte": start,
        "sourceRegionEndByteExclusive": end,
        "sourceRegionSha256": hashlib.sha256(old).hexdigest(),
        "replacementRegionSha256": hashlib.sha256(replacement).hexdigest(),
        "unchangedOutsideRegion": True,
        "scope": "native-installation-partial",
        "acceptanceScope": "supplemental-native-installation",
        "fullAcceptancePassed": False,
        "formalReleaseSmokePassed": False,
        "productExecuted": False,
        "guestModuleRelativePath": "source/desktop/scripts/" + OUTPUT_NAME,
    }


def checked_path(value: str, *, directory: bool) -> Path:
    """Reject linked/reparse ancestors before resolving an existing path."""
    candidate = Path(os.path.abspath(value))
    for component in reversed((candidate, *candidate.parents)):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(f"Linked/reparse path refused: {component}")
    info = candidate.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError(f"Expected a regular {'directory' if directory else 'file'}: {candidate}")
    return candidate.resolve(strict=True)


def prepare(source_value: str, output_value: str) -> dict[str, object]:
    source = checked_path(source_value, directory=False)
    if source.name != "p3-installation.mjs":
        raise ValueError("Source must be the original p3-installation.mjs")
    output_candidate = Path(os.path.abspath(output_value))
    if output_candidate.name != OUTPUT_NAME:
        raise ValueError(f"Output must be named {OUTPUT_NAME}")
    output = checked_path(str(output_candidate.parent), directory=True) / OUTPUT_NAME
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite an existing output: {output}")
    source_bytes = source.read_bytes()
    generated, metadata = transform(source_bytes)
    if source.read_bytes() != source_bytes:
        raise ValueError("Source changed during preparation")
    # Exclusive creation prevents overwriting an original module or earlier
    # evidence even if another preparer creates this destination concurrently.
    with output.open("xb") as stream:
        stream.write(generated)
        stream.flush()
        os.fsync(stream.fileno())
    if source.read_bytes() != source_bytes or output.read_bytes() != generated:
        raise ValueError("Source or generated output changed during preparation")
    return {**metadata, "sourcePath": str(source), "outputPath": str(output), "sourceChanged": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Pinned original desktop/scripts/p3-installation.mjs")
    parser.add_argument("--output", required=True, help=f"New staging file named {OUTPUT_NAME}; parent must exist")
    arguments = parser.parse_args()
    try:
        metadata = prepare(arguments.source, arguments.output)
    except (OSError, ValueError, AssertionError) as error:
        print(f"Native installation preparation refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
