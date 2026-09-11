// Destructive installation acceptance is restricted to a disposable Windows
// user/VM or a fresh GitHub-hosted runner. Unit tests never execute an installer.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { copyFile, lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import {
  assertReleasePermission, assertOwnedOrAbsent, claimProfileRoots, removeOwnedProfiles,
  releaseProfileRoots, sanitizedEnvironment, windowsContext, NativeSupervisor, until, checkNoLinks,
  withCleanup,
} from './p3-smoke.mjs';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const scratchMarker = '.p3-installation-owner.json';
const uninstallKey = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Chaoxing GUI Tauri';
const preferencesKey = 'Software\\chaoxing-gui\\Chaoxing GUI Tauri';
const canonical = (value) => path.win32.resolve(value).toLowerCase();
const json = async (filename) => JSON.parse(await readFile(filename, 'utf8'));
const encoded = (script) => Buffer.from(script, 'utf16le').toString('base64');

export function parseInstallationArguments(argv) {
  const names = new Map([['installer-path', 'installerPath'], ['portable-path', 'portablePath'],
    ['evidence-directory', 'evidenceDirectory'], ['powershell-path', 'powerShell'], ['timeout-seconds', 'timeoutSeconds']]);
  const result = { timeoutSeconds: 360 };
  const seen = new Set();
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index].replace(/^--/, '');
    if (!argv[index].startsWith('--') || (!names.has(flag) && flag !== 'disposable-windows-user')) throw new Error(`Unknown installation argument: ${argv[index]}`);
    if (seen.has(flag)) throw new Error(`Duplicate installation argument: --${flag}`);
    seen.add(flag);
    if (flag === 'disposable-windows-user') result.disposableWindowsUser = true;
    else {
      const value = argv[++index];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for --${flag}`);
      result[names.get(flag)] = value;
    }
  }
  result.timeoutSeconds = Number(result.timeoutSeconds);
  if (!Number.isInteger(result.timeoutSeconds) || result.timeoutSeconds < 1 || result.timeoutSeconds > 1200) throw new Error('Installation timeout must be between 1 and 1200 seconds');
  return result;
}

export async function assertInstallationPreflight(roots, records, runId, sid) {
  if (!Array.isArray(records) || records.length) throw new Error(`Installation smoke refuses preexisting Tauri registry/install records: ${JSON.stringify(records)}`);
  await assertOwnedOrAbsent(roots, runId, sid);
}

export async function validateInstallationInputs(options) {
  for (const [key, extension, label, signature] of [
    ['installerPath', '.exe', 'Installer executable', Buffer.from('MZ')],
    ['portablePath', '.zip', 'Portable ZIP', Buffer.from([0x50, 0x4b, 0x03, 0x04])],
  ]) {
    const filename = options[key];
    if (!filename || path.extname(filename).toLowerCase() !== extension) throw new Error(`${label} path must end in ${extension}`);
    const info = await lstat(filename).catch((error) => { throw new Error(`${label} missing: ${filename}: ${error.message}`); });
    if (!info.isFile() || !(await checkNoLinks(filename))) throw new Error(`${label} must be a regular file`);
    const handle = await open(filename, 'r');
    try {
      const head = Buffer.alloc(signature.length);
      await handle.read(head, 0, head.length, 0);
      assert.ok(head.equals(signature), `${label} does not have a valid ${key === 'installerPath' ? 'PE/MZ executable' : 'ZIP'} header`);
    } finally { await handle.close(); }
  }
  const sidecar = `${options.installerPath}.manifest.json`;
  assert.ok((await lstat(sidecar)).isFile() && await checkNoLinks(sidecar), 'NSIS payload sidecar must be a regular file');
}

export function nsisSpecification(mode, directory) {
  if (!['install', 'uninstall'].includes(mode)) throw new Error('Unsupported NSIS operation');
  if (typeof directory !== 'string' || !/^[A-Za-z]:\\/.test(directory) || /[\x00-\x1f"<>|?*]/.test(directory)
    || directory.slice(2).includes(':') || directory.endsWith('\\')
    || canonical(directory) !== directory.toLowerCase() || path.win32.parse(directory).root.toLowerCase() === directory.toLowerCase()) {
    throw new Error('NSIS directory must be a normalized absolute local directory path');
  }
  return { args: mode === 'install' ? ['/S', '/NS'] : ['/S'], nsisTail: { mode, directory } };
}

function registryPath(value, label) {
  if (typeof value !== 'string' || !value) throw new Error(`Missing registry ${label}`);
  const plain = value.startsWith('"') && value.endsWith('"') ? value.slice(1, -1) : value;
  if (plain.includes('"') || /[\r\n\0]/.test(plain)) throw new Error(`Invalid registry ${label}`);
  return canonical(plain);
}

export function assertInstallRegistry(records, directory) {
  assert.ok(records.some((entry) => entry.kind === 'uninstall'), 'Installer did not create its uninstall registry record');
  for (const entry of records) {
    assert.equal(entry.hive, 'CurrentUser', 'Installer must register only for CurrentUser');
    assert.ok([uninstallKey.toLowerCase(), preferencesKey.toLowerCase()].includes(entry.key.toLowerCase()), 'Unexpected installer registry key');
    assert.equal(registryPath(entry.installLocation, 'install location'), canonical(directory), 'Installer wrote an unexpected install location/directory');
    if (entry.kind === 'uninstall') assert.equal(registryPath(entry.uninstallString, 'uninstaller'), canonical(path.win32.join(directory, 'uninstall.exe')), 'Unexpected registry uninstaller');
    else assert.equal(entry.kind, 'preferences', 'Unexpected installer registry record');
  }
}

async function assertScratchOwnership(directory, runId) {
  assert.ok(await checkNoLinks(directory), 'Scratch must exist without linked ancestors');
  await checkNoLinks(path.join(directory, scratchMarker));
  const ownership = await json(path.join(directory, scratchMarker)).catch((error) => { throw new Error(`Scratch is not owned: ${error.message}`); });
  assert.equal(ownership.runId, runId, 'Scratch owner must be the current run');
  assert.equal(canonical(ownership.path), canonical(directory), 'Scratch owner path mismatch');
  const resolved = await realpath(directory);
  assert.equal(canonical(resolved), canonical(directory), 'Unexpected scratch removal target');
  assert.notEqual(canonical(directory), canonical(path.parse(directory).root), 'Refusing root directory cleanup');
  return resolved;
}

export async function removeOwnedScratch(directory, runId) {
  if (!(await checkNoLinks(directory, true))) return;
  const resolved = await assertScratchOwnership(directory, runId);
  await rm(resolved, { recursive: true, force: false, maxRetries: 4, retryDelay: 200 });
}

async function sha256(filename) {
  const hasher = createHash('sha256');
  for await (const bytes of createReadStream(filename)) hasher.update(bytes);
  return hasher.digest('hex');
}

const junctionPlacements = ['install-directory', 'backend', 'nested-backend'];

function installationJunctionPaths(scratch, placement) {
  assert.ok(junctionPlacements.includes(placement), 'Unsupported installation junction placement');
  const directory = path.join(scratch, `拒绝 ${placement} junction`);
  const installDirectory = path.join(directory, '安装 目录');
  return { directory, installDirectory, target: path.join(directory, '保留 原始数据'),
    junction: placement === 'install-directory' ? installDirectory
      : path.join(installDirectory, 'backend', ...(placement === 'nested-backend' ? ['_internal'] : [])) };
}

export async function createInstallationJunctionFixture(scratch, runId, placement) {
  await assertScratchOwnership(scratch, runId);
  await checkNoLinks(scratch, true);
  const fixture = { scratch, runId, placement, ...installationJunctionPaths(scratch, placement), sentinels: [] };
  await mkdir(fixture.directory);
  if (path.dirname(fixture.junction) !== fixture.directory) await mkdir(path.dirname(fixture.junction), { recursive: true });
  await mkdir(fixture.target);
  await mkdir(path.join(fixture.target, 'nested'));
  for (const relative of ['sentinel.txt', path.join('nested', 'sentinel.txt')]) {
    const filename = path.join(fixture.target, relative);
    await writeFile(filename, `P3 junction target must remain unchanged: ${runId}: ${relative}`, { flag: 'wx' });
    fixture.sentinels.push({ relative, before: await sha256(filename) });
  }
  // This deliberately created link is the only exception to the harness's
  // no-links rule. Both endpoints are fresh children of the owned scratch.
  await symlink(fixture.target, fixture.junction, process.platform === 'win32' ? 'junction' : 'dir');
  return fixture;
}

async function assertFixtureJunction(fixture, allowAbsent = false) {
  await assertScratchOwnership(fixture.scratch, fixture.runId);
  for (const [key, expected] of Object.entries(installationJunctionPaths(fixture.scratch, fixture.placement))) {
    assert.equal(fixture[key], expected, `Unexpected installation junction ${key}`);
  }
  assert.ok(await checkNoLinks(path.dirname(fixture.junction)), 'Junction parent must exist without links');
  assert.ok(await checkNoLinks(fixture.target, true), 'Junction target must exist without nested links');
  const info = await lstat(fixture.junction).catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
  if (!info && allowAbsent) return false;
  assert.ok(info?.isSymbolicLink(), 'Expected the captured installation junction');
  assert.equal(canonical(await realpath(fixture.junction)), canonical(fixture.target), 'Installation junction target changed');
  return true;
}

export async function assertInstallationJunctionRejected(fixture, exitCode, records) {
  assert.equal(exitCode, 2, 'Installer must reject the existing installation junction with exit code 2');
  assert.deepEqual(records, [], 'Rejected junction installation wrote Tauri registry records');
  await assertFixtureJunction(fixture);
  assert.deepEqual((await readdir(fixture.directory)).sort(), ['保留 原始数据', '安装 目录'], 'Rejected junction installation wrote unexpected fixture files');
  if (fixture.placement !== 'install-directory') {
    assert.deepEqual(await readdir(fixture.installDirectory), ['backend'], 'Rejected junction installation wrote program files');
  }
  if (fixture.placement === 'nested-backend') {
    assert.deepEqual(await readdir(path.join(fixture.installDirectory, 'backend')), ['_internal'], 'Rejected junction installation wrote backend files');
  }
  assert.deepEqual((await readdir(fixture.target)).sort(), ['nested', 'sentinel.txt'], 'Installer changed the junction target inventory');
  assert.deepEqual(await readdir(path.join(fixture.target, 'nested')), ['sentinel.txt'], 'Installer changed the nested junction target inventory');
  for (const entry of fixture.sentinels) {
    entry.after = await sha256(path.join(fixture.target, entry.relative));
    assert.equal(entry.after, entry.before, `Installer modified the junction target: ${entry.relative}`);
  }
}

export async function removeInstallationJunction(fixture) {
  if (!(await assertFixtureJunction(fixture, true))) return;
  // Unlink only this verified junction, never recursively remove through it.
  await unlink(fixture.junction);
}

export function installationRetentionFixtures(context, scratch, runId) {
  const tauriData = path.join(context.roaming, 'com.chaoxing.gui', 'data');
  const legacyData = path.join(context.roaming, 'chaoxing-desktop');
  return [
    { path: path.join(tauriData, 'web_config.json'), content: JSON.stringify({ settings: { jobs: 2 }, p3RunId: runId }) },
    { path: path.join(tauriData, 'renderer-session.json'), content: JSON.stringify({ version: 1, login: { username: 'p3-tauri-fixture' }, activeTask: null }) },
    { path: path.join(context.local, 'com.chaoxing.gui', 'EBWebView', 'Default', 'Preferences'), content: JSON.stringify({ p3RunId: runId, retained: 'WebView profile' }) },
    { path: path.join(legacyData, 'renderer-session.json'), content: JSON.stringify({ version: 1, login: { username: 'p3-old-electron-fixture' }, activeTask: null }) },
    { path: path.join(legacyData, 'cookies.txt'), content: 'P3 synthetic Electron cookie sentinel; no account credentials' },
    { path: path.join(scratch, '旧版 Electron 程序', 'chaoxing-gui.exe'), content: 'P3 inert old Electron program fixture; never execute' },
    { path: path.join(scratch, '旧版 Electron 程序', 'resources', 'app.asar'), content: 'P3 inert old Electron resources fixture' },
  ];
}

export async function assertRetainedFiles(entries) {
  for (const entry of entries) {
    entry.after = await sha256(entry.path);
    assert.equal(entry.after, entry.before, `Uninstall modified retained data/program: ${entry.path}`);
  }
}

export async function cleanupChildProfileInvocation(invocation, context) {
  if (invocation.profileCleanup?.completed) return;
  const cleanup = invocation.process?.cleanup;
  const host = invocation.process?.identity;
  if (host) {
    assert.equal(cleanup?.verified, true, 'Child profile cleanup requires a verified captured process tree');
    assert.deepEqual(cleanup.remaining, [], 'Child profile cleanup requires an empty captured Job');
    assert.ok(Array.isArray(cleanup.observed) && cleanup.observed.every((identity) => !identity.alive), 'Child profile cleanup found a live captured process');
    assert.ok(cleanup.observed.some((identity) => identity.pid === host.pid && identity.createdAtFileTime === host.createdAtFileTime),
      'Child profile cleanup must include the captured host identity');
  }
  const folders = await readdir(invocation.evidenceDirectory, { withFileTypes: true }).catch((error) => {
    if (error.code === 'ENOENT') return []; throw error;
  });
  if (folders.length === 0) {
    invocation.profileCleanup = { completed: true, claimedProfiles: false };
    return;
  }
  assert.equal(folders.length, 1, 'Each child profile handoff must belong to exactly one smoke invocation');
  assert.ok(folders[0].isDirectory() && /^smoke-[A-Za-z0-9]+$/.test(folders[0].name), 'Unexpected child profile evidence directory');
  const evidence = path.join(invocation.evidenceDirectory, folders[0].name);
  const handoff = path.join(evidence, 'profile-ownership.json');
  if (!(await checkNoLinks(handoff))) {
    invocation.profileCleanup = { completed: true, claimedProfiles: false };
    return;
  }
  const ownership = await json(handoff);
  assert.equal(ownership.schemaVersion, 1, 'Unsupported child profile ownership schema');
  assert.equal(ownership.configuration, 'Release', 'Child profile handoff must be for Release');
  assert.equal(ownership.sid, context.sid, 'Child profile SID mismatch');
  assert.ok(typeof ownership.runId === 'string' && /^[a-f0-9-]{36}$/.test(ownership.runId), 'Invalid child profile run ID');
  assert.equal(canonical(ownership.evidenceDirectory), canonical(evidence), 'Child profile evidence path mismatch');
  const roots = releaseProfileRoots(context);
  assert.deepEqual(ownership.roots, roots, 'Child profile paths must match actual Windows known folders');
  assert.ok(host, 'Child profile cleanup requires a captured host identity');
  invocation.profileCleanup = { completed: false, runId: ownership.runId, sid: ownership.sid, roots, capturedTreeVerified: true };
  await removeOwnedProfiles(roots, ownership.runId, ownership.sid);
  invocation.profileCleanup.completed = true;
}

// Keep registry paths explicit. Inspection emits only matching Tauri records,
// not an inventory of unrelated installed software.
const inspectRegistryScript = String.raw`
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p3Records = [Collections.Generic.List[object]]::new()
foreach ($p3Hive in @('CurrentUser','LocalMachine')) {
  foreach ($p3View in @('Registry64','Registry32')) {
    $p3Base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]$p3Hive, [Microsoft.Win32.RegistryView]$p3View)
    try {
      $p3PreferencesPath = 'Software\chaoxing-gui\Chaoxing GUI Tauri'
      $p3Preferences = $p3Base.OpenSubKey($p3PreferencesPath, $false)
      if ($null -ne $p3Preferences) {
        try { $p3Records.Add(@{ hive=$p3Hive; view=$p3View; key=$p3PreferencesPath; kind='preferences'; installLocation=[string]$p3Preferences.GetValue('') }) }
        finally { $p3Preferences.Dispose() }
      }
      $p3UninstallRoot = $p3Base.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall', $false)
      if ($null -ne $p3UninstallRoot) {
        try {
          foreach ($p3Name in $p3UninstallRoot.GetSubKeyNames()) {
            $p3Entry = $p3UninstallRoot.OpenSubKey($p3Name, $false)
            if ($null -eq $p3Entry) { continue }
            try {
              $p3Display = [string]$p3Entry.GetValue('DisplayName')
              $p3Binary = [string]$p3Entry.GetValue('MainBinaryName')
              if ($p3Name -in @('Chaoxing GUI Tauri','com.chaoxing.gui') -or $p3Display -like '*Chaoxing GUI Tauri*' -or $p3Binary -eq 'chaoxing-gui-tauri.exe') {
                $p3Records.Add(@{ hive=$p3Hive; view=$p3View; key="Software\Microsoft\Windows\CurrentVersion\Uninstall\$p3Name"; kind='uninstall'; displayName=$p3Display; installLocation=[string]$p3Entry.GetValue('InstallLocation'); uninstallString=[string]$p3Entry.GetValue('UninstallString') })
              }
            } finally { $p3Entry.Dispose() }
          }
        } finally { $p3UninstallRoot.Dispose() }
      }
      $p3Run = $p3Base.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Run', $false)
      if ($null -ne $p3Run) {
        try {
          if ('Chaoxing GUI Tauri' -in $p3Run.GetValueNames()) { $p3Records.Add(@{ hive=$p3Hive; view=$p3View; key='Software\Microsoft\Windows\CurrentVersion\Run'; kind='autorun'; valueName='Chaoxing GUI Tauri' }) }
        } finally { $p3Run.Dispose() }
      }
    } finally { $p3Base.Dispose() }
  }
}
$p3Local = [Environment]::GetFolderPath('LocalApplicationData')
$p3ProgramFiles = [Environment]::GetFolderPath('ProgramFiles')
$p3ProgramFilesX86 = [Environment]::GetFolderPath('ProgramFilesX86')
$p3Programs = [Environment]::GetFolderPath('Programs')
$p3Desktop = [Environment]::GetFolderPath('DesktopDirectory')
@{ records=@($p3Records.ToArray()); defaultPaths=@(
  (Join-Path $p3Local 'Chaoxing GUI Tauri'), (Join-Path $p3Local 'Programs\Chaoxing GUI Tauri'),
  (Join-Path $p3ProgramFiles 'Chaoxing GUI Tauri'), (Join-Path $p3ProgramFilesX86 'Chaoxing GUI Tauri'),
  (Join-Path $p3Programs 'Chaoxing GUI Tauri'), (Join-Path $p3Programs 'Chaoxing GUI Tauri.lnk'),
  (Join-Path $p3Desktop 'Chaoxing GUI Tauri.lnk')
) } | ConvertTo-Json -Depth 8 -Compress
`;

async function installationState(context) {
  const { stdout } = await exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded(inspectRegistryScript)], { windowsHide: true, timeout: 20000 });
  return JSON.parse(stdout);
}

// A verified archive is still extracted into a fresh owned directory with a
// second traversal/link check. No archive-controlled path reaches the source.
const extractScript = String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
if (Test-Path -LiteralPath $p3Request.destination) { throw 'Extraction destination must not exist' }
$p3Zip = [IO.Compression.ZipFile]::OpenRead($p3Request.archive)
try {
  if ($p3Zip.Entries.Count -gt 100000) { throw 'Too many ZIP entries' }
  $p3Names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  [long]$p3Length = 0
  foreach ($p3Entry in $p3Zip.Entries) {
    $p3Name = $p3Entry.FullName.TrimEnd('/')
    if (-not $p3Name -or $p3Name -match '(^/|\\|:|(^|/)\.{1,2}(/|$)|[<>"|?*\x00-\x1f])' -or -not $p3Names.Add($p3Name)) { throw "Unsafe ZIP path: $p3Name" }
    foreach ($p3Component in $p3Name.Split('/')) { if (-not $p3Component -or $p3Component.EndsWith(' ') -or $p3Component.EndsWith('.')) { throw 'Unsafe ZIP path component' } }
    if ((($p3Entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000 -or ($p3Entry.ExternalAttributes -band 0x400)) { throw 'ZIP symbolic link/reparse entry refused' }
    $p3Length += $p3Entry.Length
    if ($p3Entry.Length -gt 2GB -or $p3Length -gt 8GB) { throw 'ZIP size limit exceeded' }
  }
} finally { $p3Zip.Dispose() }
[IO.Compression.ZipFile]::ExtractToDirectory($p3Request.archive, $p3Request.destination, $false)
`;

// NSIS rewrites Tauri's bundle marker and can sign the host independently of
// the portable binary. Keep its exact, artifact-bound expectation separately.
const createNsisReferenceScript = String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
. $p3Request.packageCommon
. $p3Request.nsisContent
$p3Scratch = Get-PackageFullPath $p3Request.scratch
Assert-PackageNoReparse $p3Scratch
$p3Owner = Read-PackageJson (Join-Path $p3Scratch '.p3-installation-owner.json')
if ($p3Owner.runId -cne $p3Request.runId -or [IO.Path]::GetFullPath($p3Owner.path) -ine $p3Scratch) { throw 'NSIS reference requires current scratch ownership' }
$p3Reference = Join-Path $p3Scratch 'nsis-payload-reference.json'
Assert-PackageChildPath $p3Reference $p3Scratch
if (Test-Path -LiteralPath $p3Reference) { throw 'NSIS reference must not already exist' }
$p3PortableManifest = Read-PackageJson $p3Request.portableManifest
if ((Get-FileHash -LiteralPath $p3Request.portableManifest -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p3Request.portableManifestSha256) { throw 'Portable reference payload manifest changed' }
$p3NsisManifest = Read-NsisPayloadManifest -InstallerPath $p3Request.installerPath -PortablePath $p3Request.portablePath -PortableManifest $p3PortableManifest
Assert-PackageManifestHeader $p3NsisManifest 'chaoxing-gui-tauri-nsis-payload' (Get-PackageVersion) 'chaoxing-gui-tauri.exe'
Write-PackageJson $p3Reference $p3NsisManifest
@{ manifest=$p3Reference; sha256=(Get-FileHash -LiteralPath $p3Reference -Algorithm SHA256).Hash.ToLowerInvariant() } | ConvertTo-Json -Compress
`;

// Reuse the packaging verifier's exact directory, length, hash, and reparse
// checks against the actual extracted/installed files before and after launch.
const verifyLayoutScript = String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
. $p3Request.packageCommon
if ($p3Request.mode -cnotin @('Portable','Installed')) { throw 'Invalid layout verification mode' }
$p3Version = Get-PackageVersion
$p3Manifest = Read-PackageJson $p3Request.manifest
$p3ManifestHash = (Get-FileHash -LiteralPath $p3Request.manifest -Algorithm SHA256).Hash.ToLowerInvariant()
if ($p3ManifestHash -cne $p3Request.manifestSha256) { throw 'Reference payload manifest changed' }
$p3ManifestKind = if ($p3Request.mode -ceq 'Portable') { 'chaoxing-gui-tauri-portable' } else { 'chaoxing-gui-tauri-nsis-payload' }
Assert-PackageManifestHeader $p3Manifest $p3ManifestKind $p3Version 'chaoxing-gui-tauri.exe'
if ($p3Manifest.platform -cne 'windows-x64') { throw 'Unexpected payload platform' }
$p3Inventory = Get-PackageInventory $p3Request.directory
$p3ExtraName = if ($p3Request.mode -ceq 'Portable') { 'package-manifest.json' } else { 'uninstall.exe' }
$p3Extra = @($p3Inventory.files | Where-Object { $_.path -ceq $p3ExtraName })
if ($p3Extra.Count -ne 1 -or $p3Extra[0].length -le 0) { throw "Missing layout file: $p3ExtraName" }
if ($p3Request.mode -ceq 'Portable' -and $p3Extra[0].sha256 -cne $p3ManifestHash) { throw 'Extracted package manifest mismatch' }
$p3Payload = @{ files=@($p3Inventory.files | Where-Object { $_.path -cne $p3ExtraName }); directories=@($p3Inventory.directories) }
Assert-PackageInventoryManifest $p3Manifest $p3Payload
$p3BackendManifest = Read-PackageJson (Join-Path $p3Request.directory 'backend-manifest.json')
Assert-PackageManifestHeader $p3BackendManifest 'chaoxing-backend' $p3Version 'chaoxing-backend.exe'
$p3BackendInventory = @{
  files=@(foreach ($p3File in $p3Inventory.files) {
    if ($p3File.path.StartsWith('backend/', [StringComparison]::Ordinal)) {
      @{ path=$p3File.path.Substring(8); length=$p3File.length; sha256=$p3File.sha256 }
    }
  })
  directories=@(foreach ($p3Directory in $p3Inventory.directories) {
    if ($p3Directory.StartsWith('backend/', [StringComparison]::Ordinal)) { $p3Directory.Substring(8) }
  })
}
Assert-PackageInventoryManifest $p3BackendManifest $p3BackendInventory
Assert-BackendLayout (Join-Path $p3Request.directory 'backend') $p3BackendInventory
@{ directory=$p3Request.directory; mode=$p3Request.mode; manifestKind=$p3ManifestKind; version=$p3Version; files=$p3Payload.files.Count; backendFiles=$p3BackendInventory.files.Count; manifestSha256=$p3ManifestHash } | ConvertTo-Json -Compress
`;

const removeRegistryScript = String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
$p3Owner = Get-Content -Raw -LiteralPath (Join-Path $p3Request.scratch '.p3-installation-owner.json') | ConvertFrom-Json
if ($p3Owner.runId -cne $p3Request.runId -or [IO.Path]::GetFullPath($p3Owner.path) -ine [IO.Path]::GetFullPath($p3Request.scratch)) { throw 'Registry cleanup requires current scratch ownership' }
$p3Expected = [IO.Path]::GetFullPath($p3Request.installDirectory)
if (-not $p3Expected.StartsWith(([IO.Path]::GetFullPath($p3Request.scratch).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Registry cleanup target is outside scratch' }
foreach ($p3Record in $p3Request.records) {
  if ($p3Record.hive -ne 'CurrentUser' -or $p3Record.view -notin @('Registry32','Registry64') -or $p3Record.key -notin @('Software\Microsoft\Windows\CurrentVersion\Uninstall\Chaoxing GUI Tauri','Software\chaoxing-gui\Chaoxing GUI Tauri')) { throw 'Unowned registry cleanup key' }
  $p3Base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryView]([string]$p3Record.view))
  try {
    $p3Entry = $p3Base.OpenSubKey($p3Record.key, $false)
    if ($null -eq $p3Entry) { continue }
    try {
      $p3Value = if ($p3Record.kind -eq 'uninstall') { [string]$p3Entry.GetValue('InstallLocation') } else { [string]$p3Entry.GetValue('') }
      $p3Value = $p3Value.Trim('"')
      if (-not $p3Value -or [IO.Path]::GetFullPath($p3Value) -ine $p3Expected) { throw 'Registry cleanup refuses an unexpected install location' }
    } finally { $p3Entry.Dispose() }
    # Only these exact absent-at-baseline subkeys, validated against the owned
    # install directory, are removed. The publisher/root keys are preserved.
    $p3Base.DeleteSubKeyTree($p3Record.key, $false)
  } finally { $p3Base.Dispose() }
}
`;

// Exposed for parser-only regression checks; importing this module performs no
// registry reads/writes, extraction, or process launches.
export const installationPowerShellScripts = { inspectRegistryScript, extractScript, createNsisReferenceScript, verifyLayoutScript, removeRegistryScript };

async function inventoryFiles(root, relative = '') {
  const files = [];
  for (const entry of await readdir(path.join(root, relative), { withFileTypes: true })) {
    const next = path.join(relative, entry.name);
    if (entry.isDirectory()) files.push(...await inventoryFiles(root, next));
    else files.push(next);
  }
  return files;
}

export async function installationMain(argv = process.argv.slice(2)) {
  const evidenceIndex = argv.indexOf('--evidence-directory');
  const requested = evidenceIndex >= 0 && argv[evidenceIndex + 1] && !argv[evidenceIndex + 1].startsWith('--')
    ? argv[evidenceIndex + 1] : path.join(repo, 'desktop/src-tauri/target/p3-installation-evidence');
  await mkdir(path.resolve(requested), { recursive: true });
  const evidence = await mkdtemp(path.join(path.resolve(requested), 'installation-'));
  const result = { runId: randomUUID(), startedAt: new Date().toISOString(), success: false, processes: [], checks: [],
    limitations: ['Installer and release-host execution is allowed only in a disposable Windows profile', 'This harness is not proof of a remote CI run until its real installer checks complete'] };
  const failures = [];
  const record = (name, details = {}) => { result.checks.push({ name, result: 'PASS', ...details }); console.log(`PASS ${name}`); };
  let context;
  let options;
  let scratch;
  let installDirectory;
  const junctionFixtures = [];
  const childProfileInvocations = [];
  let baseline;
  let preflightPassed = false;
  let retentionRoots = [];
  let sequenceCompleted = false;
  let operationNumber = 0;

  async function runChecked(name, executable, args, extra = {}) {
    const owner = new NativeSupervisor(context.powerShell, path.join(evidence, `${++operationNumber}-${name}`));
    const expectedExitCode = extra.expectedExitCode ?? 0;
    const item = { name, executable, args, nsisTail: extra.nsisTail || null, expectedExitCode };
    result.processes.push(item);
    if (extra.profileInvocation) extra.profileInvocation.process = item;
    try {
      item.identity = await owner.start({ executable, args, cwd: scratch || repo,
        env: extra.env || { ...process.env }, ...(extra.nsisTail ? { nsisTail: extra.nsisTail } : {}) });
      item.shutdown = await until(async () => {
        const snapshot = await owner.snapshot();
        if (snapshot.host && !snapshot.host.alive && snapshot.host.exitCode !== expectedExitCode) {
          const error = new Error(`${name} exited ${snapshot.host.exitCode}`); error.fatal = true; throw error;
        }
        return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive) && snapshot;
      }, `${name} process and descendants`, options.timeoutSeconds * 1000, 200);
      assert.equal(item.shutdown.host.exitCode, expectedExitCode, `${name} must exit with the expected code and without a pending reboot`);
    } finally {
      try { item.cleanup = await owner.dispose(); }
      catch (error) { item.cleanup = owner.cleanup || { verified: false }; throw error; }
    }
    assert.equal(item.cleanup.fallbackUsed, false, `${name} required forced cleanup`);
    assert.equal(item.cleanup.verified, true, `${name} process cleanup was not verified`);
    return item;
  }

  const runScript = (name, script, args = [], extra = {}) => runChecked(name, context.powerShell, ['-NoProfile', '-NonInteractive', '-File', path.join(repo, 'desktop/scripts', script), ...args], extra);
  const runInline = (name, script, request) => runChecked(name, context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded(script)],
    { env: { ...process.env, P3_INSTALLATION_REQUEST: JSON.stringify(request) } });

  async function smokeLayout(name, directory) {
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

  try {
    options = parseInstallationArguments(argv);
    // This must precede windowsContext, input execution, archive extraction,
    // registry mutation, and every installer/host launch.
    result.permission = assertReleasePermission({ configuration: 'Release', disposableWindowsUser: options.disposableWindowsUser });
    context = await windowsContext(options.powerShell);
    baseline = await installationState(context);
    const roots = [...releaseProfileRoots(context), ...baseline.defaultPaths.map((entry) => ({ path: entry, claim: false }))];
    await assertInstallationPreflight(roots, baseline.records, result.runId, context.sid);
    result.baseline = { ...baseline, checkedProfileRoots: roots, sid: context.sid };
    if (options.installerPath) options.installerPath = path.resolve(options.installerPath);
    if (options.portablePath) options.portablePath = path.resolve(options.portablePath);
    await validateInstallationInputs(options);
    preflightPassed = true;
    result.sources = await Promise.all([options.installerPath, options.portablePath, `${options.installerPath}.manifest.json`]
      .map(async (filename) => ({ path: filename, sha256: await sha256(filename) })));
    scratch = await mkdtemp(path.join(context.temp, 'chaoxing-p3 安装 空格-'));
    await writeFile(path.join(scratch, scratchMarker), JSON.stringify({ runId: result.runId, path: scratch }), { flag: 'wx' });
    result.scratch = scratch;
    installDirectory = path.join(scratch, '安装 目录');
    const portableDirectory = path.join(scratch, '便携 解压');
    await runScript('verify-portable', 'verify-package.ps1', ['-PackagePath', options.portablePath]);
    await runScript('verify-nsis', 'verify-nsis.ps1', ['-InstallerPath', options.installerPath, '-PortablePath', options.portablePath, '-EvidenceDirectory', path.join(evidence, 'nsis-content')]);
    result.junctionRejections = junctionFixtures;
    for (const placement of junctionPlacements) {
      const fixture = await createInstallationJunctionFixture(scratch, result.runId, placement);
      junctionFixtures.push(fixture);
      const rejectedInstall = nsisSpecification('install', fixture.installDirectory);
      const rejectedProcess = await runChecked(`reject-${placement}-junction`, options.installerPath, rejectedInstall.args,
        { nsisTail: rejectedInstall.nsisTail, expectedExitCode: 2, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
      fixture.exitCode = rejectedProcess.shutdown.host.exitCode;
      fixture.registry = (await installationState(context)).records;
      await assertInstallationJunctionRejected(fixture, fixture.exitCode, fixture.registry);
      await assertInstallationPreflight(roots, fixture.registry, result.runId, context.sid);
      await removeInstallationJunction(fixture);
      fixture.linkCleanup = true;
      record(`installer-rejects-preexisting-${placement}-junction`, { exitCode: fixture.exitCode, sentinels: fixture.sentinels });
    }
    await runInline('extract-portable', extractScript, { archive: options.portablePath, destination: portableDirectory });
    await checkNoLinks(portableDirectory, true);
    const portableManifest = path.join(portableDirectory, 'package-manifest.json');
    const portableManifestSha256 = await sha256(portableManifest);
    await runInline('create-nsis-reference', createNsisReferenceScript, { packageCommon: path.join(repo, 'desktop/scripts/package-common.ps1'),
      nsisContent: path.join(repo, 'desktop/scripts/nsis-content.ps1'), scratch, runId: result.runId,
      installerPath: options.installerPath, portablePath: options.portablePath, portableManifest, portableManifestSha256 });
    const nsisManifest = path.join(scratch, 'nsis-payload-reference.json');
    await checkNoLinks(nsisManifest);
    const references = {
      Portable: { manifest: portableManifest, manifestSha256: portableManifestSha256 },
      Installed: { manifest: nsisManifest, manifestSha256: await sha256(nsisManifest) },
    };
    result.referenceManifests = references;
    const verifyLayout = async (name, directory, mode) => {
      const reference = references[mode];
      assert.ok(reference, `Unsupported layout verification mode: ${mode}`);
      await runInline(name, verifyLayoutScript, { packageCommon: path.join(repo, 'desktop/scripts/package-common.ps1'),
        directory, mode, ...reference });
      record(name, { directory, mode, ...reference });
    };
    await verifyLayout('portable-manifest-before-host', portableDirectory, 'Portable');
    await smokeLayout('portable', portableDirectory);
    await verifyLayout('portable-manifest-after-host', portableDirectory, 'Portable');

    await assertInstallationPreflight(roots, (await installationState(context)).records, result.runId, context.sid);
    const install = nsisSpecification('install', installDirectory);
    await runChecked('install-nsis', options.installerPath, install.args, { nsisTail: install.nsisTail, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    const installedState = await installationState(context);
    result.installedRegistry = installedState.records;
    assertInstallRegistry(installedState.records, installDirectory);
    // Any default-path write is a failure, even if a correct owned-path registry
    // record was also written. Unexpected paths are never recursively cleaned.
    await assertOwnedOrAbsent(baseline.defaultPaths.map((entry) => ({ path: entry, claim: false })), result.runId, context.sid);
    await checkNoLinks(installDirectory, true);
    const installedFiles = await inventoryFiles(installDirectory);
    assert.ok(installedFiles.includes('chaoxing-gui-tauri.exe') && installedFiles.includes('uninstall.exe'), 'NSIS did not install the host and uninstaller at the requested location');
    result.installedFiles = installedFiles;
    await verifyLayout('installed-manifest-before-host', installDirectory, 'Installed');
    await smokeLayout('installed', installDirectory);
    await verifyLayout('installed-manifest-after-host', installDirectory, 'Installed');

    // Both real-host smokes have removed only their owned AppData profiles.
    // Create new synthetic retained data only after all launches have ended.
    const legacyData = path.win32.join(context.roaming, 'chaoxing-desktop');
    retentionRoots = releaseProfileRoots(context).map((entry) => ({ ...entry, claim: entry.claim || canonical(entry.path) === canonical(legacyData) }));
    await claimProfileRoots(retentionRoots, result.runId, context.sid);
    const retained = installationRetentionFixtures(context, scratch, result.runId);
    for (const entry of retained) { await mkdir(path.dirname(entry.path), { recursive: true }); await writeFile(entry.path, entry.content, { flag: 'wx' }); }
    result.retention = await Promise.all(retained.map(async (entry) => ({ path: entry.path, before: await sha256(entry.path) })));
    const uninstaller = path.join(scratch, '卸载 程序.exe');
    await copyFile(path.join(installDirectory, 'uninstall.exe'), uninstaller);
    const uninstall = nsisSpecification('uninstall', installDirectory);
    await runChecked('uninstall-nsis', uninstaller, uninstall.args, { nsisTail: uninstall.nsisTail, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    await assertRetainedFiles(result.retention);
    for (const relative of installedFiles) assert.equal(await lstat(path.join(installDirectory, relative)).catch((error) => { if (error.code === 'ENOENT') return null; throw error; }), null, `Uninstall left installed file: ${relative}`);
    assert.equal(await lstat(installDirectory).catch((error) => { if (error.code === 'ENOENT') return null; throw error; }), null, 'Uninstall left its program directory');
    result.afterUninstallRegistry = (await installationState(context)).records;
    assert.deepEqual(result.afterUninstallRegistry, [], 'Uninstall left Tauri registry/install records');
    record('uninstall-retains-tauri-data-and-old-electron', { retention: result.retention, installedFilesRemoved: installedFiles.length, remainingRegistryRecords: 0 });
    sequenceCompleted = true;
  } catch (error) { failures.push(error.stack || String(error)); }
  finally {
    if (preflightPassed && scratch) {
      try {
        const current = await installationState(context);
        const owned = [];
        for (const directory of [installDirectory, ...junctionFixtures.map((fixture) => fixture.installDirectory)].filter(Boolean)) {
          const matching = current.records.filter((entry) => entry.hive === 'CurrentUser' && [uninstallKey.toLowerCase(), preferencesKey.toLowerCase()].includes(entry.key.toLowerCase())
            && typeof entry.installLocation === 'string' && registryPath(entry.installLocation, 'cleanup install location') === canonical(directory));
          if (matching.length) {
            assert.equal(baseline.records.length, 0, 'Registry cleanup requires empty baseline');
            await runInline('cleanup-owned-registry', removeRegistryScript, { scratch, runId: result.runId, installDirectory: directory, records: matching });
            owned.push(...matching);
          }
        }
        result.registryCleanup = { removedOwnedRecords: owned, remainingRecords: (await installationState(context)).records };
        if (result.registryCleanup.remainingRecords.length) throw new Error('Unexpected Tauri registry records remain; no unowned records were deleted');
      } catch (error) { failures.push(`Registry cleanup: ${error.stack || error}`); }
      for (const invocation of childProfileInvocations) {
        try { await cleanupChildProfileInvocation(invocation, context); }
        catch (error) { failures.push(`Child profile cleanup: ${error.stack || error}`); }
      }
      if (retentionRoots.length) {
        try { await removeOwnedProfiles(retentionRoots, result.runId, context.sid); result.retentionFixtureCleanup = true; }
        catch (error) { failures.push(`Profile fixture cleanup: ${error.stack || error}`); }
      }
      for (const fixture of junctionFixtures) {
        try { await removeInstallationJunction(fixture); fixture.linkCleanup = true; }
        catch (error) { failures.push(`Junction fixture cleanup: ${error.stack || error}`); }
      }
      try { await removeOwnedScratch(scratch, result.runId); result.scratchCleanup = true; }
      catch (error) { failures.push(`Scratch cleanup: ${error.stack || error}`); }
    }
    for (const source of result.sources || []) {
      try { source.after = await sha256(source.path); assert.equal(source.after, source.sha256, 'Installation smoke modified an input artifact'); }
      catch (error) { failures.push(`Source preservation: ${error.stack || error}`); }
    }
    result.success = sequenceCompleted && failures.length === 0;
    result.childProfileCleanup = childProfileInvocations.map(({ name, evidenceDirectory, profileCleanup }) => ({ name, evidenceDirectory, ...profileCleanup }));
    if (failures.length) { result.error = failures.join('\n\n'); console.error(result.error); }
    result.endedAt = new Date().toISOString();
    await writeFile(path.join(evidence, 'result.json'), JSON.stringify(result, null, 2), { flag: 'wx' });
    console.log(`Evidence: ${path.join(evidence, 'result.json')}`);
  }
  return result.success ? 0 : 1;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) process.exitCode = await installationMain();
