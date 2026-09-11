import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import test, { before, after } from 'node:test';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const installerFile = path.join(desktop, 'src-tauri/windows/installer.nsi');
const hooksFile = path.join(desktop, 'src-tauri/windows/installer-hooks.nsh');
const fixtureFile = path.join(desktop, 'tests/fixtures/nsis-paths.nsi');
const digest = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
let buildRoot;
let fixtureInstaller;
let fixtureUninstaller;
let stringLimit;

function compilerPath() {
  const candidates = [
    process.env.NSIS_MAKENSIS,
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'tauri/NSIS/makensis.exe'),
    process.env['ProgramFiles(x86)'] && path.join(process.env['ProgramFiles(x86)'], 'NSIS/makensis.exe'),
    process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'NSIS/makensis.exe'),
    ...(process.env.PATH || '').split(path.delimiter).filter(Boolean)
      .map(directory => path.join(directory.replace(/^"|"$/g, ''), 'makensis.exe')),
  ];
  return candidates.find(file => file && fs.existsSync(file));
}

const compiler = process.platform === 'win32' ? compilerPath() : undefined;
const required = process.env.CHAOXING_REQUIRE_NSIS_PATH_TESTS === '1';
const skipReason = process.platform !== 'win32' ? 'requires Windows junctions and NSIS'
  : !compiler ? 'makensis.exe unavailable; set NSIS_MAKENSIS or build Tauri first (CHAOXING_REQUIRE_NSIS_PATH_TESTS=1 forbids this skip)'
    : false;
const windowsTest = (name, callback) => test(name, { skip: skipReason && !required }, callback);

function terminateTree(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  // Kill the tree while the root is still alive; spawnSync's own timeout would
  // kill only the root first and could orphan an NSIS/compiler helper process.
  const killed = spawnSync(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32/taskkill.exe'),
    ['/PID', String(child.pid), '/T', '/F'], {
      windowsHide: true, stdio: 'pipe', encoding: 'utf8', timeout: 10_000,
    });
  if (killed.status !== 0) child.kill('SIGKILL');
}

async function run(executable, args, env = {}, verbatim = false) {
  const child = spawn(executable, args, {
    cwd: buildRoot,
    env: { ...process.env, TEMP: path.join(buildRoot, 'temp'), TMP: path.join(buildRoot, 'temp'), ...env },
    windowsHide: true,
    windowsVerbatimArguments: verbatim,
    stdio: 'pipe',
  });
  let stdout = '';
  let stderr = '';
  let timer;
  child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
  child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
  try {
    return await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('close', status => resolve({ status, output: `${stdout}\n${stderr}` }));
      timer = setTimeout(() => {
        terminateTree(child);
        reject(new Error(`${path.basename(executable)} timed out after 30 seconds; its process tree was terminated`));
      }, 30_000);
    });
  } finally {
    clearTimeout(timer);
    terminateTree(child);
  }
}

function renderedPathTable() {
  const template = fs.readFileSync(installerFile, 'utf8');
  const block = template.match(/; CHAOXING_PATH_TABLE_BEGIN\r?\n([\s\S]*?); CHAOXING_PATH_TABLE_END/);
  // Baseline compiles the old hooks too, so junction negatives run red before
  // the production table/checker is implemented. The structural test requires it.
  if (!block) return '!macro ChaoxingPayloadPathChecks PREFIX\n!macroend\n';
  const values = {
    resources_dirs: ['backend', 'backend\\_internal', ''],
    // Tauri emits an empty ancestor for INSTDIR itself. The root check must
    // cover it without treating it as an invalid empty relative destination.
    resources_ancestors: ['backend\\_internal', 'backend', ''],
    resources: [['unused-source', 'backend\\_internal\\payload.bin']],
    binaries: ['fixture-helper.exe'],
  };
  const rendered = block[1].replace(/{{#each (\w+)}}([\s\S]*?){{\/each}}/g, (_, name, body) => {
    assert.ok(Object.hasOwn(values, name), `unhandled production path table: ${name}`);
    return values[name].map(value => body
      .replace(/{{#if this}}([\s\S]*?){{\/if}}/g, (_, block) => value ? block : '')
      .replaceAll('{{this.[1]}}', Array.isArray(value) ? value[1] : value)
      .replaceAll('{{this}}', Array.isArray(value) ? value[1] : value)).join('');
  });
  assert.ok(!rendered.includes('{{'), 'every production path-table placeholder must be rendered');
  return rendered;
}

before(async () => {
  if (required) assert.equal(skipReason, false, String(skipReason));
  if (skipReason) return;
  buildRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'cx-nsis-path-build-'));
  fs.mkdirSync(path.join(buildRoot, 'temp'));
  const table = path.join(buildRoot, 'payload-paths.nsh');
  fs.writeFileSync(table, renderedPathTable());
  fixtureInstaller = path.join(buildRoot, 'guard-install.exe');
  fixtureUninstaller = path.join(buildRoot, 'guard-uninstall.exe');
  const compiled = await run(compiler, [
    '/V2', `/DFIXTURE_OUTPUT=${fixtureInstaller}`, `/DFIXTURE_HOOKS=${hooksFile}`,
    `/DFIXTURE_PATH_TABLE=${table}`, fixtureFile,
  ]);
  assert.equal(compiled.status, 0, compiled.output);
  const bootstrap = await run(fixtureInstaller, ['/S'], { CHAOXING_NSIS_FIXTURE_BOOTSTRAP: '1' });
  assert.equal(bootstrap.status, 0, bootstrap.output);
  assert.ok(fs.existsSync(fixtureUninstaller));
  stringLimit = Number(fs.readFileSync(path.join(buildRoot, 'string-limit.txt'), 'utf8'));
  assert.ok(Number.isInteger(stringLimit) && stringLimit > 255, 'fixture reports its compiled NSIS string limit');
});

after(() => {
  if (!buildRoot) return;
  assert.ok(path.basename(buildRoot).startsWith('cx-nsis-path-build-'));
  fs.rmSync(buildRoot, { recursive: true, force: true });
});

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cx-nsis-path-case-'));
  const links = [];
  t.after(() => {
    for (const link of links.reverse()) {
      if (fs.existsSync(link) || fs.lstatSync(link, { throwIfNoEntry: false })) {
        assert.ok(fs.lstatSync(link).isSymbolicLink(), `refusing to remove a replaced junction: ${link}`);
        fs.unlinkSync(link);
      }
    }
    assert.ok(path.basename(root).startsWith('cx-nsis-path-case-'));
    fs.rmSync(root, { recursive: true, force: true });
  });
  const target = path.join(root, '安装 目录');
  const external = path.join(root, 'outside sentinel');
  seed(target);
  seed(external);
  return {
    root, target, external,
    junction(link, destination) {
      assert.ok(path.resolve(link).startsWith(root + path.sep));
      assert.ok(path.resolve(destination).startsWith(root + path.sep));
      fs.symlinkSync(destination, link, 'junction');
      links.push(link);
    },
    invoke(mode, directory = target, { extra = '', checkOnly = false, legacyTree = false, msi = false } = {}) {
      // Malformed-path tests do not perform any payload writes even on a broken guard.
      if (!checkOnly) assert.ok(path.resolve(directory).startsWith(root + path.sep));
      const env = {
        CHAOXING_NSIS_FIXTURE_ROOT: directory,
        CHAOXING_NSIS_FIXTURE_EXTRA: extra,
        CHAOXING_NSIS_FIXTURE_CHECK_ONLY: checkOnly ? '1' : '0',
        CHAOXING_NSIS_FIXTURE_LEGACY_TREE: legacyTree ? '1' : '0',
        CHAOXING_NSIS_FIXTURE_MSI: msi ? '1' : '0',
        CHAOXING_NSIS_FIXTURE_BOOTSTRAP: '0',
      };
      return mode === 'install'
        ? run(fixtureInstaller, ['/S'], env)
        // NSIS consumes _?= through the end, without ordinary argv quotes.
        : run(fixtureUninstaller, ['/S', `_?=${buildRoot}`], env, true);
    },
  };
}

function seed(directory) {
  fs.mkdirSync(path.join(directory, 'backend/_internal'), { recursive: true });
  fs.writeFileSync(path.join(directory, 'fixture-host.exe'), 'fixture host before guard');
  fs.writeFileSync(path.join(directory, 'backend/_internal/payload.bin'), 'nested sentinel before guard');
  fs.writeFileSync(path.join(directory, 'preserved-data.txt'), 'synthetic data, must remain');
}

function snapshot(directory) {
  const inventory = [];
  function visit(current, prefix) {
    for (const name of fs.readdirSync(current).sort()) {
      const file = path.join(current, name);
      const relative = prefix ? `${prefix}/${name}` : name;
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink()) inventory.push([relative, 'link', fs.readlinkSync(file)]);
      else if (stat.isDirectory()) { inventory.push([relative, 'directory']); visit(file, relative); }
      else inventory.push([relative, digest(file)]);
    }
  }
  visit(directory, '');
  return inventory;
}

test('NSIS production table checks every emitted payload kind before install and uninstall mutations', () => {
  const text = fs.readFileSync(installerFile, 'utf8');
  assert.match(text, /CHAOXING_PATH_TABLE_BEGIN/);
  for (const name of ['resources_dirs', 'resources', 'resources_ancestors', 'binaries']) {
    assert.ok(renderedPathTable().includes('ChaoxingCheckPath'), name);
    assert.match(text.match(/CHAOXING_PATH_TABLE_BEGIN([\s\S]*?)CHAOXING_PATH_TABLE_END/)[1], new RegExp(`{{#each ${name}}}`));
  }
  const early = text.slice(text.indexOf('Section EarlyChecks'), text.indexOf('Section WebView2'));
  assert.match(early, /NSIS_HOOK_PREINSTALL/);
  const install = text.slice(text.indexOf('Section Install'), text.indexOf('Function .onInstSuccess'));
  assert.ok(install.indexOf('NSIS_HOOK_PREINSTALL') < install.indexOf('SetOutPath'));
  assert.doesNotMatch(install, /ReadRegStr \$OldMainBinaryName/);
  assert.match(text, /ReadRegStr \$OldMainBinaryName[\s\S]*?Call ChaoxingCheckPath/);
  assert.match(text, /Call ChaoxingCheckLegacyInstallTree[\s\S]*?ExecWait '\$R1'/);
  const reinstall = text.slice(text.indexOf('Function PageLeaveReinstall'), text.indexOf('; 5. Choose install directory page'));
  assert.match(reinstall, /\$WixMode = 1\s+Call ChaoxingRefuseMsiMigration/);
  assert.doesNotMatch(reinstall, /ReadRegStr \$R1 HKLM/);
  const uninstall = text.slice(text.indexOf('Section Uninstall'), text.indexOf('Function RestorePreviousInstallLocation'));
  assert.ok(uninstall.indexOf('NSIS_HOOK_PREUNINSTALL') < uninstall.indexOf('Delete '));
  assert.match(text, /MUI_FINISHPAGE_RUN_NOTCHECKED/);
  assert.match(text, /ExecWait '\"\$6\" \$\{WEBVIEW2INSTALLERARGS\} \/install'/);
});

windowsTest('NSIS guards allow normal Chinese and space paths for install and uninstall while retaining data', async t => {
  const f = fixture(t);
  const installed = await f.invoke('install');
  assert.equal(installed.status, 0, installed.output);
  assert.equal(fs.readFileSync(path.join(f.target, 'backend/_internal/payload.bin'), 'utf8'), 'installed fixture payload');
  const uninstalled = await f.invoke('uninstall');
  assert.equal(uninstalled.status, 0, uninstalled.output);
  assert.ok(!fs.existsSync(path.join(f.target, 'backend/_internal/payload.bin')));
  assert.equal(fs.readFileSync(path.join(f.target, 'preserved-data.txt'), 'utf8'), 'synthetic data, must remain');
});

windowsTest('NSIS guards allow a fresh directory, repeated separators and ordinary dot filenames', async t => {
  const f = fixture(t);
  const fresh = path.join(f.root, '全新 父目录', '应用');
  assert.equal((await f.invoke('install', fresh)).status, 0);
  assert.equal((await f.invoke('uninstall', fresh)).status, 0);
  for (const extra of ['backend\\\\_internal//payload.bin', 'backend/.hidden.txt', 'backend/name .txt']) {
    assert.equal((await f.invoke('install', f.target + '\\', { extra, checkOnly: true })).status, 0, extra);
  }
});

for (const mode of ['install', 'uninstall']) {
  for (const relative of ['', 'backend', 'backend/_internal']) {
    windowsTest(`NSIS ${mode} rejects ${relative || 'installation directory'} junction before any payload mutation`, async t => {
      const f = fixture(t);
      const link = relative ? path.join(f.target, relative) : f.target;
      const moved = path.join(f.root, 'moved original');
      fs.renameSync(link, moved);
      const destination = relative ? path.join(f.external, relative) : f.external;
      f.junction(link, destination);
      const beforeExternal = snapshot(f.external);
      const beforeTarget = relative ? snapshot(f.target) : null;
      const result = await f.invoke(mode);
      assert.equal(result.status, 2, result.output);
      assert.deepEqual(snapshot(f.external), beforeExternal);
      if (relative) assert.deepEqual(snapshot(f.target), beforeTarget);
      assert.ok(fs.lstatSync(link).isSymbolicLink());
    });
  }
}

windowsTest('NSIS checks ancestors above INSTDIR and existing host/uninstaller leaf paths', async t => {
  const f = fixture(t);
  const alias = path.join(f.root, 'parent alias');
  f.junction(alias, f.external);
  for (const mode of ['install', 'uninstall']) {
    const result = await f.invoke(mode, path.join(alias, 'not-created'), { checkOnly: true });
    assert.equal(result.status, 2, result.output);
    for (const filename of ['fixture-host.exe', 'uninstall.exe', 'fixture-helper.exe']) {
      const file = path.join(f.target, filename);
      if (fs.existsSync(file)) fs.unlinkSync(file);
      f.junction(file, f.external);
      assert.equal((await f.invoke(mode, f.target, { checkOnly: true })).status, 2, filename);
      fs.unlinkSync(file);
    }
  }
});

windowsTest('NSIS guards reject relative roots, traversal, ADS, device paths and file/directory collisions', async t => {
  const f = fixture(t);
  for (const mode of ['install', 'uninstall']) {
    for (const root of ['relative-root', path.parse(f.target).root, '\\\\?\\' + f.target,
      '\\\\.\\' + f.target, '\\\\localhost\\share\\app', 'C:relative', f.root + '\\..\\outside']) {
      assert.equal((await f.invoke(mode, root, { checkOnly: true })).status, 2, root);
    }
    for (const extra of ['..\\outside.txt', 'backend\\..\\outside.txt', 'backend/.\\outside.txt',
      'backend/file:stream', 'NUL.txt', 'backend/COM1', 'conin$', 'lPt².txt', 'backend/NUL .txt',
      'backend/trailing.', 'backend/trailing ', 'backend/*', 'backend/?', 'backend/bad\u0001name',
      'backend/bad\tname', 'backend/file"name', '\\rooted.txt', 'C:\\absolute.txt', 'backend/', 'a'.repeat(stringLimit - 1)]) {
      assert.equal((await f.invoke(mode, f.target, { extra, checkOnly: true })).status, 2, JSON.stringify(extra.slice(0, 80)));
    }
  }
  fs.rmSync(path.join(f.target, 'backend'), { recursive: true });
  fs.writeFileSync(path.join(f.target, 'backend'), 'a file cannot be a payload parent directory');
  assert.equal((await f.invoke('install', f.target, { checkOnly: true })).status, 2);
  assert.equal((await f.invoke('uninstall', f.target, { checkOnly: true })).status, 2);
});

windowsTest('NSIS checks the entire previous install tree before invoking an older uninstaller', async t => {
  const f = fixture(t);
  const legacyParent = path.join(f.target, 'backend/_internal/removed-package');
  fs.mkdirSync(legacyParent);
  fs.writeFileSync(path.join(legacyParent, 'legacy.bin'), 'old package, absent from the new manifest');
  assert.equal((await f.invoke('install', f.target, { legacyTree: true, checkOnly: true })).status, 0);
  for (const relative of ['redirected-dir', 'removed-package/redirected-dir', 'removed-package/old-file.bin']) {
    const link = path.join(f.target, 'backend/_internal', relative);
    f.junction(link, f.external);
    const beforeExternal = snapshot(f.external);
    const beforeTarget = snapshot(f.target);
    const result = await f.invoke('install', f.target, { legacyTree: true });
    assert.equal(result.status, 2, relative);
    assert.deepEqual(snapshot(f.external), beforeExternal);
    assert.deepEqual(snapshot(f.target), beforeTarget);
    assert.ok(fs.lstatSync(link).isSymbolicLink());
    fs.unlinkSync(link);
  }
});

windowsTest('NSIS stops a legacy-tree scan at its depth limit and refuses unverified MSI migration', async t => {
  const f = fixture(t);
  fs.mkdirSync(path.join(f.target, ...Array(65).fill('d')), { recursive: true });
  const beforeTarget = snapshot(f.target);
  assert.equal((await f.invoke('install', f.target, { legacyTree: true })).status, 2);
  assert.deepEqual(snapshot(f.target), beforeTarget);
  assert.equal((await f.invoke('install', f.target, { msi: true })).status, 2);
  assert.deepEqual(snapshot(f.target), beforeTarget);
});
