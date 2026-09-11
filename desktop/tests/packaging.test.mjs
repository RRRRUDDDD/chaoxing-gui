import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const project = path.dirname(desktop);
const version = fs.readFileSync(path.join(project, 'pyproject.toml'), 'utf8').match(/^version\s*=\s*"([^"]+)"/m)[1];
const windowsTest = process.platform === 'win32' ? test : test.skip;
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const psQuote = (value) => `'${String(value).replaceAll("'", "''")}'`;

function runPowerShell(args, executable = 'pwsh.exe') {
  const result = spawnSync(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', ...args], {
    encoding: 'utf8', windowsHide: true, timeout: 30_000, maxBuffer: 8 * 1024 * 1024,
  });
  assert.ifError(result.error);
  return { ...result, output: `${result.stdout}\n${result.stderr}` };
}

function script(name, args = []) {
  return runPowerShell(['-File', path.join(desktop, 'scripts', name), ...args]);
}

function psCode(code) {
  return runPowerShell(['-EncodedCommand', Buffer.from(`$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); ${code}`, 'utf16le').toString('base64')]);
}

function success(result) {
  assert.equal(result.status, 0, result.output);
}

function failure(result, message) {
  assert.notEqual(result.status, 0, `Unexpected success: ${result.output}`);
  assert.match(result.output, message);
}

function write(root, relative, bytes) {
  const target = path.join(root, relative);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, bytes);
}

function copyFixtureTree(source, destination) {
  // Node 24.14's native cpSync crashes on these Unicode Windows paths; exercise
  // the same fixture bytes through the stable mkdir/copyFile primitives.
  fs.mkdirSync(destination, { recursive: true });
  for (const item of fs.readdirSync(source, { withFileTypes: true })) {
    assert.equal(item.isSymbolicLink(), false);
    const from = path.join(source, item.name);
    const to = path.join(destination, item.name);
    if (item.isDirectory()) copyFixtureTree(from, to);
    else fs.copyFileSync(from, to);
  }
}

function removeWithin(root, target) {
  const resolvedRoot = path.resolve(root);
  const resolvedTarget = path.resolve(target);
  assert.ok(resolvedTarget.startsWith(`${resolvedRoot}${path.sep}`), 'cleanup must stay inside its fixture');
  fs.rmSync(resolvedTarget, { recursive: true, force: true });
}

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'chaoxing packaging 中文-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('chaoxing packaging 中文-'));
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const source = path.join(directory, 'frozen source 中文');
  write(source, 'chaoxing-backend.exe', 'MZ fake frozen backend; never executed');
  write(source, '_internal/python311.dll', 'fake python runtime');
  write(source, '_internal/base_library.zip', 'fake standard library');
  write(source, '_internal/ddddocr/模型/common.onnx', Buffer.from([0, 255, 3, 17]));
  write(source, '_internal/web/dist/index.html', '<script src="/assets/main.js"></script><link href="/assets/main.css" rel="stylesheet">');
  write(source, '_internal/web/dist/assets/main.js', 'console.log("packaging fixture");');
  write(source, '_internal/web/dist/assets/main.css', 'body { color: black; }');
  write(source, '_internal/web/dist/assets/nested 中文/lazy.js', 'export const fixture = true;');
  write(source, '_internal/.metadata', 'hidden-name fixture');
  fs.mkdirSync(path.join(source, '_internal/empty directory 中文'), { recursive: true });
  const destination = path.join(directory, 'staged resources 中文/backend');
  const manifest = path.join(path.dirname(destination), 'backend-manifest.json');
  const host = path.join(directory, 'release host 中文/chaoxing-gui-tauri.exe');
  write(path.dirname(host), path.basename(host), 'MZ fake Tauri host; never executed by packaging');
  const output = path.join(directory, 'release output 中文');
  const artifact = path.join(output, `chaoxing-gui-tauri-portable-${version}-windows-x64.zip`);
  return { directory, source, destination, manifest, host, output, artifact };
}

function snapshot(root) {
  const result = {};
  function visit(directory, prefix = '') {
    for (const item of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const relative = `${prefix}${item.name}`;
      const filename = path.join(directory, item.name);
      assert.equal(item.isSymbolicLink(), false);
      if (item.isDirectory()) {
        result[`${relative}/`] = 'directory';
        visit(filename, `${relative}/`);
      } else {
        result[relative] = sha256(fs.readFileSync(filename));
      }
    }
  }
  visit(root);
  return result;
}

function stage(f, args = []) {
  return script('prepare-backend.ps1', ['-SourceDirectory', f.source, '-DestinationDirectory', f.destination, ...args]);
}

function pack(f, args = []) {
  return script('package-portable.ps1', ['-HostPath', f.host, '-BackendDirectory', f.destination, '-OutputDirectory', f.output, ...args]);
}

function verify(f, args = []) {
  return script('verify-package.ps1', ['-PackagePath', f.artifact, ...args]);
}

function zipEntries(filename) {
  const result = psCode(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead(${psQuote(filename)})
    try {
      $rows = @(foreach ($entry in $zip.Entries) {
        $stream = $entry.Open()
        $memory = [IO.MemoryStream]::new()
        try { $stream.CopyTo($memory); @{ path = $entry.FullName; bytes = [Convert]::ToBase64String($memory.ToArray()) } }
        finally { $memory.Dispose(); $stream.Dispose() }
      })
      ConvertTo-Json -InputObject $rows -Compress
    } finally { $zip.Dispose() }
  `);
  success(result);
  return new Map(JSON.parse(result.stdout.trim()).map((entry) => [entry.path, Buffer.from(entry.bytes, 'base64')]));
}

function alterZip(f, entryName, content) {
  const writeEntry = content === null ? '' : `
    $replacement = $zip.CreateEntry(${psQuote(entryName)})
    $stream = $replacement.Open()
    try { $bytes = [Convert]::FromBase64String(${psQuote(Buffer.from(content).toString('base64'))}); $stream.Write($bytes, 0, $bytes.Length) }
    finally { $stream.Dispose() }
  `;
  success(psCode(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::Open(${psQuote(f.artifact)}, [IO.Compression.ZipArchiveMode]::Update)
    try { $entry = $zip.GetEntry(${psQuote(entryName)}); if ($null -ne $entry) { $entry.Delete() }; ${writeEntry} }
    finally { $zip.Dispose() }
  `));
  // Refresh only the outer checksum: the verifier must independently validate the payload.
  const filename = `${f.artifact}.manifest.json`;
  const manifest = JSON.parse(fs.readFileSync(filename, 'utf8'));
  const bytes = fs.readFileSync(f.artifact);
  manifest.artifact.sha256 = sha256(bytes);
  manifest.artifact.length = bytes.length;
  fs.writeFileSync(filename, JSON.stringify(manifest));
  fs.writeFileSync(`${f.artifact}.sha256`, `${manifest.artifact.sha256}  ${path.basename(f.artifact)}\n`);
}

windowsTest('staging preserves the complete onedir tree, hashes, empty directories and source bytes', (t) => {
  const f = fixture(t);
  const before = snapshot(f.source);
  success(stage(f));
  assert.deepEqual(snapshot(f.destination), before);
  assert.deepEqual(snapshot(f.source), before);
  const manifest = JSON.parse(fs.readFileSync(f.manifest, 'utf8'));
  assert.equal(manifest.schemaVersion, 1);
  assert.equal(manifest.version, version);
  assert.equal(manifest.entryPoint, 'chaoxing-backend.exe');
  assert.deepEqual(new Set(manifest.directories), new Set(Object.keys(before).filter((name) => name.endsWith('/')).map((name) => name.slice(0, -1))));
  assert.equal(manifest.files.length, Object.keys(before).filter((name) => !name.endsWith('/')).length);
  for (const file of manifest.files) {
    assert.equal(file.sha256, before[file.path]);
    assert.equal(file.length, fs.statSync(path.join(f.source, file.path)).size);
    assert.equal(path.isAbsolute(file.path), false);
    assert.equal(file.path.includes('\\'), false);
  }
  write(f.destination, '_internal/obsolete.dll', 'stale staging');
  write(f.source, '_internal/web/dist/assets/main.js', 'updated web build');
  success(stage(f));
  assert.deepEqual(snapshot(f.destination), snapshot(f.source));
  assert.deepEqual(fs.readdirSync(path.dirname(f.destination)).sort(), ['backend', 'backend-manifest.json']);
});

windowsTest('missing frozen executable, _internal, HTML, assets or referenced chunks fail without replacing staging', (t) => {
  const f = fixture(t);
  success(stage(f));
  const previous = snapshot(path.dirname(f.destination));
  for (const missing of ['chaoxing-backend.exe', '_internal', '_internal/web/dist/index.html', '_internal/web/dist/assets', '_internal/web/dist/assets/main.js']) {
    const invalid = path.join(f.directory, `invalid ${missing.replaceAll('/', '-')}`);
    copyFixtureTree(f.source, invalid);
    removeWithin(invalid, path.join(invalid, missing));
    const before = snapshot(invalid);
    failure(stage({ ...f, source: invalid }), /missing|required|resource|asset|internal|incomplete/i);
    assert.deepEqual(snapshot(path.dirname(f.destination)), previous, missing);
    assert.deepEqual(snapshot(invalid), before, missing);
  }
});

windowsTest('overlapping source/destination and filesystem roots are refused before mutation', (t) => {
  const f = fixture(t);
  const before = snapshot(f.source);
  for (const destination of [f.source, path.join(f.source, 'backend'), f.directory, path.parse(f.source).root]) {
    failure(stage({ ...f, destination }), /overlap|unsafe|root|source|protected/i);
    assert.deepEqual(snapshot(f.source), before);
  }
});

windowsTest('a locked manifest rolls back an already replaced staging directory', (t) => {
  const f = fixture(t);
  success(stage(f));
  const previous = snapshot(path.dirname(f.destination));
  write(f.source, '_internal/new generation.dll', 'next build');
  const source = snapshot(f.source);
  const result = psCode(`
    $lock = [IO.File]::Open(${psQuote(f.manifest)}, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try { & ${psQuote(path.join(desktop, 'scripts/prepare-backend.ps1'))} -SourceDirectory ${psQuote(f.source)} -DestinationDirectory ${psQuote(f.destination)} }
    finally { $lock.Dispose() }
  `);
  failure(result, /process|access|used|move|manifest|exception|文件|进程/i);
  assert.deepEqual(snapshot(path.dirname(f.destination)), previous);
  assert.deepEqual(snapshot(f.source), source);
});

windowsTest('publication survives a transient Windows directory sharing lock without changing the source', (t) => {
  const f = fixture(t);
  success(stage(f));
  write(f.destination, 'previous generation.txt', 'previous stage');
  const candidate = path.join(path.dirname(f.destination), '.candidate backend 中文');
  copyFixtureTree(f.source, candidate);
  const source = snapshot(f.source);
  const result = psCode(`
    . ${psQuote(path.join(desktop, 'scripts/package-common.ps1'))}
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32.SafeHandles;
public sealed class PackagingRenameLock : IDisposable {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    private readonly SafeFileHandle handle;
    private readonly Timer timer;
    public PackagingRenameLock(string path) {
        // FILE_LIST_DIRECTORY, FILE_SHARE_READ | WRITE (deliberately no DELETE).
        handle = CreateFileW(path, 1, 3, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
        if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
        timer = new Timer(_ => handle.Dispose(), null, 1000, Timeout.Infinite);
    }
    public void Dispose() { timer.Dispose(); handle.Dispose(); }
}
'@
    $held = [PackagingRenameLock]::new(${psQuote(candidate)})
    try {
      Publish-PackageItems -AllowedParent ${psQuote(path.dirname(f.destination))} -Items @(
        @{ Source = ${psQuote(candidate)}; Destination = ${psQuote(f.destination)} }
      )
    } finally { $held.Dispose() }
  `);
  success(result);
  assert.deepEqual(snapshot(f.destination), source);
  assert.deepEqual(snapshot(f.source), source);
  assert.deepEqual(fs.readdirSync(path.dirname(f.destination)).sort(), ['backend', 'backend-manifest.json']);
});

windowsTest('source, staging and ancestor junctions are refused without touching their targets', (t) => {
  const f = fixture(t);
  const outside = path.join(f.directory, 'unrelated target');
  write(outside, 'keep.txt', 'must survive');
  const before = snapshot(outside);
  const sourceLink = path.join(f.source, '_internal/junction');
  fs.symlinkSync(outside, sourceLink, 'junction');
  failure(stage(f), /reparse|junction|symbolic/i);
  fs.unlinkSync(sourceLink);
  fs.mkdirSync(path.dirname(f.destination), { recursive: true });
  fs.symlinkSync(outside, f.destination, 'junction');
  failure(stage(f), /reparse|junction|symbolic/i);
  fs.unlinkSync(f.destination);
  const ancestor = path.join(f.directory, 'ancestor junction');
  fs.symlinkSync(outside, ancestor, 'junction');
  failure(stage({ ...f, destination: path.join(ancestor, 'backend') }), /reparse|junction|symbolic/i);
  assert.deepEqual(snapshot(outside), before);
});

windowsTest('version drift and unsafe version text fail before staging or artifact publication', (t) => {
  const f = fixture(t);
  for (const badVersion of ['9.9.9', '../1.1.1', '1.1.1-beta']) {
    failure(stage(f, ['-Version', badVersion]), /version/i);
    assert.equal(fs.existsSync(f.destination), false);
  }
  success(stage(f));
  failure(pack(f, ['-Version', '9.9.9']), /version/i);
  assert.equal(fs.existsSync(f.artifact), false);
});

windowsTest('portable ZIP preserves backend hierarchy and verifies artifact and every payload file without launching it', (t) => {
  const f = fixture(t);
  const before = snapshot(f.source);
  success(stage(f));
  const staged = snapshot(path.dirname(f.destination));
  success(pack(f));
  success(verify(f));
  const entries = zipEntries(f.artifact);
  for (const required of ['chaoxing-gui-tauri.exe', 'backend-manifest.json', 'package-manifest.json', 'Start-Chaoxing.cmd', 'Start-Chaoxing.ps1', 'Install-WebView2.cmd', 'Install-WebView2.ps1', 'README.txt', 'LICENSE', 'TAURI-LICENSE.txt']) {
    assert.ok(entries.has(required), required);
  }
  assert.deepEqual(entries.get('LICENSE'), fs.readFileSync(path.join(project, 'LICENSE')));
  assert.deepEqual(entries.get('TAURI-LICENSE.txt'), fs.readFileSync(path.join(desktop, 'src-tauri/windows/LICENSE_MIT')));
  const payload = JSON.parse(entries.get('package-manifest.json').toString('utf8'));
  assert.equal(payload.version, version);
  for (const file of payload.files) {
    assert.ok(entries.has(file.path), `ZIP is missing manifest path ${file.path}`);
    assert.equal(sha256(entries.get(file.path)), file.sha256, file.path);
    assert.equal(entries.get(file.path).length, file.length, file.path);
  }
  for (const [name, hash] of Object.entries(before)) {
    const bytes = entries.get(`backend/${name}`);
    assert.ok(bytes, name);
    if (hash !== 'directory') assert.equal(sha256(bytes), hash, name);
  }
  assert.equal([...entries.keys()].some((name) => /electron|tests\/|^_internal\//i.test(name)), false);
  assert.deepEqual(snapshot(f.source), before);
  assert.deepEqual(snapshot(path.dirname(f.destination)), staged);
  const outer = JSON.parse(fs.readFileSync(`${f.artifact}.manifest.json`, 'utf8'));
  assert.equal(outer.artifact.sha256, sha256(fs.readFileSync(f.artifact)));
  assert.match(fs.readFileSync(`${f.artifact}.sha256`, 'utf8'), new RegExp(`^${outer.artifact.sha256}  `));
});

windowsTest('tampered staging, missing manifest and backend version drift preserve previous published artifacts', (t) => {
  const f = fixture(t);
  success(stage(f));
  success(pack(f));
  const published = snapshot(f.output);
  const originalManifest = fs.readFileSync(f.manifest);
  for (const change of [
    () => fs.writeFileSync(f.manifest, '{}'),
    () => fs.writeFileSync(f.manifest, JSON.stringify({ ...JSON.parse(originalManifest), version: '9.9.9' })),
    () => fs.unlinkSync(f.manifest),
    () => write(f.destination, '_internal/ddddocr/模型/common.onnx', 'tampered model'),
  ]) {
    change();
    failure(pack(f), /manifest|version|hash|length|mismatch/i);
    assert.deepEqual(snapshot(f.output), published);
    fs.writeFileSync(f.manifest, originalManifest);
  }
});

windowsTest('ZIP verifier rejects outer checksum drift, inner tampering, missing payload and traversal entries', (t) => {
  const f = fixture(t);
  success(stage(f));
  success(pack(f));
  const originals = new Map(fs.readdirSync(f.output).map((name) => [name, fs.readFileSync(path.join(f.output, name))]));
  const restore = () => { for (const [name, bytes] of originals) fs.writeFileSync(path.join(f.output, name), bytes); };
  fs.appendFileSync(f.artifact, 'corrupt trailing data');
  failure(verify(f), /artifact|hash|length|checksum/i);
  restore();
  for (const [entry, content] of [
    ['backend/_internal/ddddocr/模型/common.onnx', 'tampered model'],
    ['backend/chaoxing-backend.exe', null],
    ['LICENSE', null],
    ['../escape.txt', 'unsafe path'],
    ['package-manifest.json', JSON.stringify({ schemaVersion: 1, version: '9.9.9' })],
  ]) {
    alterZip(f, entry, content);
    failure(verify(f), /manifest|hash|length|missing|payload|unsafe|path|version/i);
    restore();
  }
  failure(verify(f, ['-Version', '9.9.9']), /version/i);
});

windowsTest('Windows PowerShell portable entrypoints preserve check/start errors and never install a runtime automatically', (t) => {
  const f = fixture(t);
  const portable = path.join(f.directory, 'portable launch 中文');
  copyFixtureTree(path.join(desktop, 'portable'), portable);
  const source = path.join(portable, 'FakeHost.cs');
  fs.writeFileSync(source, `using System; using System.IO; using System.Threading; using System.Diagnostics;
    class FakeHost { static int Main(string[] args) {
      string root = AppDomain.CurrentDomain.BaseDirectory;
      if (args.Length == 1 && args[0] == "--child") {
        File.WriteAllText(Path.Combine(root, "child-pid.txt"), Process.GetCurrentProcess().Id.ToString());
        Thread.Sleep(60000); return 0;
      }
      bool check = args.Length == 1 && args[0] == "--check-webview2";
      File.AppendAllText(Path.Combine(root, "calls.txt"), check ? "check\\n" : "start\\n");
      string file = Path.Combine(root, check ? "check-code.txt" : "start-code.txt");
      int code = File.Exists(file) ? int.Parse(File.ReadAllText(file)) : 0;
      if (check && code == -999) {
        File.WriteAllText(Path.Combine(root, "parent-pid.txt"), Process.GetCurrentProcess().Id.ToString());
        var child = new ProcessStartInfo(Path.Combine(root, "chaoxing-gui-tauri.exe"), "--child");
        child.UseShellExecute = false; child.CreateNoWindow = true; child.RedirectStandardInput = true;
        using (var process = Process.Start(child)) { process.StandardInput.Close(); Thread.Sleep(60000); }
      }
      return code;
    } }`);
  const host = path.join(portable, 'chaoxing-gui-tauri.exe');
  const compiler = path.join(process.env.WINDIR, 'Microsoft.NET/Framework64/v4.0.30319/csc.exe');
  const compiled = spawnSync(compiler, ['/nologo', '/target:exe', `/out:${host}`, source], { encoding: 'utf8', windowsHide: true, timeout: 20_000 });
  assert.ifError(compiled.error);
  assert.equal(compiled.status, 0, `${compiled.stdout}\n${compiled.stderr}`);
  const windowsPowerShell = path.join(process.env.WINDIR, 'System32/WindowsPowerShell/v1.0/powershell.exe');
  const launch = () => runPowerShell(['-File', path.join(portable, 'Start-Chaoxing.ps1')], windowsPowerShell);
  for (const [check, start, expected, calls] of [[3, 0, 3, 'check\n'], [17, 0, 17, 'check\n'], [0, 23, 23, 'check\nstart\n'], [0, 0, 0, 'check\nstart\n']]) {
    write(portable, 'check-code.txt', String(check));
    write(portable, 'start-code.txt', String(start));
    write(portable, 'calls.txt', '');
    const result = launch();
    assert.equal(result.status, expected, result.output);
    assert.equal(fs.readFileSync(path.join(portable, 'calls.txt'), 'utf8'), calls);
  }
  write(portable, 'check-code.txt', '-999');
  const timedOut = runPowerShell(['-File', path.join(portable, 'Start-Chaoxing.ps1'), '-CheckTimeoutSeconds', '1'], windowsPowerShell);
  failure(timedOut, /timed\s+out/i);
  for (const name of ['parent-pid.txt', 'child-pid.txt']) {
    const pid = Number(fs.readFileSync(path.join(portable, name), 'utf8'));
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' }, `${name} should be stopped`);
  }
  const offline = path.join(portable, 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe');
  fs.copyFileSync(host, offline);
  const before = fs.readFileSync(path.join(portable, 'calls.txt'), 'utf8');
  const rejected = runPowerShell(['-File', path.join(portable, 'Install-WebView2.ps1'), '-InstallerPath', offline], windowsPowerShell);
  failure(rejected, /signature|Microsoft|Authenticode/i);
  assert.equal(fs.readFileSync(path.join(portable, 'calls.txt'), 'utf8'), before, 'unsigned installer must never execute');
});
