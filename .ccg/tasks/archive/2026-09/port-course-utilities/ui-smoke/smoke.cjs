const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createServer } = require('node:http');
const { chromium } = require('../../desktop/node_modules/playwright-core');

const evidenceDir = __dirname;
const distDir = path.resolve(__dirname, '../../web/dist');
const report = { startedAt: new Date().toISOString(), fixtureOnly: true, viewports: [], screenshots: [], cleanup: {} };
const courseId = 'smoke-course-one';
const courses = [
  { courseId, title: '高等数学 · 第一学期' },
  { courseId: 'smoke-course-two', title: '大学英语 · 阅读与听力' },
];
const resources = [
  { id: 'smoke-video', course_id: courseId, course_title: courses[0].title, chapter_id: 'chapter-one', chapter_title: '第一章 · 函数与极限', name: '函数与极限：课程导学', kind: 'video', watchable: true, downloadable: true, duration: 20 },
  { id: 'smoke-extra-video', course_id: courseId, course_title: courses[0].title, chapter_id: 'chapter-two', chapter_title: '第二章 · 微分的定义和实际应用', name: '微分的概念与例题', kind: 'video', watchable: true, downloadable: true, duration: 70 },
  { id: 'smoke-document', course_id: courseId, course_title: courses[0].title, chapter_id: 'chapter-one', chapter_title: '第一章 · 函数与极限', name: '复习讲义_2026_MathematicsCourseResourceWithAnIntentionallyLongUnbrokenFilenameForResponsiveLayout.pdf', kind: 'document', watchable: false, downloadable: true },
  { id: 'smoke-audio', course_id: courseId, course_title: courses[0].title, chapter_id: 'chapter-one', chapter_title: '第一章 · 函数与极限', name: '课程音频：课堂要点回顾', kind: 'audio', watchable: false, downloadable: true, duration: 120 },
  { id: 'smoke-locked', course_id: courseId, course_title: courses[0].title, chapter_id: 'chapter-two', chapter_title: '第二章 · 微分的定义和实际应用', name: '暂不可用的视频资源', kind: 'video', watchable: false, downloadable: false },
];
let fixture;
let server;
let browser;
let origin;

const success = (data) => ({ status: 200, body: { status: true, data } });
const failure = (status, msg) => ({ status, body: { status: false, msg } });
function taskDetail(task) {
  if (task.task_type === 'catalog') return {
    courses: [], active_jobs: {}, tool: {
      purpose: task.purpose, course_ids: [courseId], resources, results: [],
      completed_units: 2, total_units: 2, unit: '章节', current: null,
    },
  };
  if (task.task_type === 'visits') return {
    courses: [], active_jobs: {}, tool: {
      purpose: 'visits', course_ids: [courseId], resources: [],
      completed_units: 2, total_units: 2, unit: '次', current: null,
      results: [{ id: courseId, name: courses[0].title, course_title: courses[0].title, status: 'completed', message: '两次请求已提交，平台统计可能延迟更新。', submitted: 2, before: 7, after: 8 }],
    },
  };
  if (task.task_type === 'video_time') return {
    courses: [], active_jobs: {}, tool: {
      purpose: 'video_time', course_ids: [courseId], resources: [],
      completed_units: 3, total_units: 6, unit: '秒',
      current: task.status === 'running' ? { name: resources[0].name, completed: 3, total: 6, unit: '秒' } : null,
      results: task.status === 'running' ? [] : [{ id: 'smoke-video', name: resources[0].name, course_title: courses[0].title, status: 'skipped', message: '模拟停止后保留已记录的时长。', seconds: 3 }],
    },
  };
  return {
    courses: [], active_jobs: {}, tool: {
      purpose: 'download', course_ids: [courseId], resources: [],
      completed_units: 2048, total_units: null, unit: '字节', current: null,
      output_dir: `E:\\FixtureDownloads\\${task.id}`,
      results: [
        { id: 'smoke-document', name: resources[2].name, course_title: courses[0].title, status: 'completed', message: '模拟文件保存完成。', bytes: 2048, path: `E:\\FixtureDownloads\\${task.id}\\MathematicsCourseResourceWithAnIntentionallyLongUnbrokenFilenameForResponsiveLayout.pdf` },
        { id: 'smoke-audio', name: resources[3].name, course_title: courses[0].title, status: 'error', message: '模拟资源读取中断，未保留不完整文件，请重新读取后重试。', bytes: 0 },
      ],
    },
  };
}

function fixtureApi(method, pathname, payload) {
  fixture.calls.push({ method, pathname, payload });
  if (pathname === '/api/login') {
    assert.equal(payload.username, 'fixture-account');
    return success({ username: payload.username });
  }
  if (pathname === '/api/config') return success({});
  if (pathname === '/api/courses') return success(courses);
  if (pathname === '/api/start') {
    assert.deepEqual(payload.course_list, [courseId]);
    assert.equal(payload.username, 'fixture-account');
    assert.equal(payload.use_cookies, true);
    assert.equal(payload.password, '');
    if (payload.task_type === 'video_time' && fixture.failVideoStart) {
      fixture.failVideoStart = false;
      return failure(404, '模拟来源任务已过期，请重新读取资源列表后重试。');
    }
    if (payload.task_type === 'visits') assert.deepEqual(payload.tool_options, { count: 2, interval: 1 });
    if (payload.task_type === 'video_time') {
      assert.equal(payload.tool_options.minutes, 0.1);
      assert.deepEqual(payload.tool_options.resource_ids, ['smoke-video']);
      assert.equal(fixture.tasks.get(payload.tool_options.source_task_id).purpose, 'video_time');
    }
    if (payload.task_type === 'download') {
      assert.deepEqual(payload.tool_options.resource_ids, ['smoke-document', 'smoke-audio']);
      assert.equal(fixture.tasks.get(payload.tool_options.source_task_id).purpose, 'download');
    }
    const id = `smoke-${payload.task_type}-${fixture.tasks.size + 1}`;
    const status = payload.task_type === 'video_time' ? 'running' : payload.task_type === 'download' ? 'partial' : 'completed';
    const labels = { catalog: '资源读取', visits: '学习次数', video_time: '视频时长', download: '资源下载' };
    const task = { id, task_id: id, task_type: payload.task_type, task_label: labels[payload.task_type], purpose: payload.tool_options.purpose, status, progress: status === 'running' ? 0 : 1, total: 1, start_time: Date.now() / 1000, current_course: courses[0].title, current_chapter: '第一章 · 函数与极限', current_task: payload.task_type === 'video_time' ? resources[0].name : '' };
    fixture.tasks.set(id, task);
    return success({ task_id: id });
  }
  const route = pathname.match(/^\/api\/task\/([^/]+)(?:\/(.+))?$/);
  if (route) {
    const task = fixture.tasks.get(route[1]);
    if (!task) return failure(404, '模拟任务不存在');
    if (task.finishStopAt && Date.now() >= task.finishStopAt) { task.status = 'cancelled'; delete task.finishStopAt; }
    if (route[2] === 'details') return success(taskDetail(task));
    if (route[2] === 'stop') {
      assert.deepEqual(payload, { username: 'fixture-account' });
      task.cancel_requested = true;
      task.finishStopAt = Date.now() + 250;
      return success({ task_id: task.id, state: 'stopping' });
    }
    if (route[2] === 'resume') return success({ task_id: task.id, status: task.status });
    if (route[2] === 'open-downloads') {
      assert.deepEqual(payload, { username: 'fixture-account' });
      assert.equal(task.task_type, 'download');
      fixture.openAttempts += 1;
      if (fixture.openAttempts === 1) return failure(500, '模拟文件管理器启动失败，请重试。');
      fixture.opened.push(task.id);
      return success({ path: taskDetail(task).tool.output_dir });
    }
    return success(task);
  }
  if (pathname.startsWith('/api/logs/')) return { status: 200, body: { status: true, data: [{ seq: 1, timestamp: Date.now() / 1000, level: 'info', message: '离线测试夹具：没有访问平台或下载真实文件。' }], next_cursor: 1, truncated: false } };
  return failure(404, `Unexpected fixture route: ${method} ${pathname}`);
}

async function startServer() {
  server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://127.0.0.1');
      if (url.pathname.startsWith('/api/')) {
        const chunks = [];
        for await (const chunk of request) chunks.push(chunk);
        const text = Buffer.concat(chunks).toString('utf8');
        const result = fixtureApi(request.method, url.pathname, text ? JSON.parse(text) : null);
        response.writeHead(result.status, { 'Content-Type': 'application/json; charset=utf-8' });
        response.end(JSON.stringify(result.body));
        return;
      }
      const filename = path.resolve(distDir, `.${url.pathname === '/' ? '/index.html' : decodeURIComponent(url.pathname)}`);
      if (!filename.startsWith(`${distDir}${path.sep}`)) throw new Error('Static path outside fixture root');
      const contentType = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.jpg': 'image/jpeg', '.png': 'image/png' }[path.extname(filename)] || 'application/octet-stream';
      response.writeHead(200, { 'Content-Type': contentType });
      response.end(fs.readFileSync(filename));
    } catch (error) {
      fixture?.serverErrors.push(error.stack);
      if (!response.headersSent) response.writeHead(500, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ status: false, msg: error.message }));
    }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  origin = `http://127.0.0.1:${server.address().port}`;
}

async function snapshot(page, run, name) {
  // Normalize sticky positions before full-page captures after locator clicks scroll.
  await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' }));
  const metrics = await page.evaluate(() => {
    const viewport = window.innerWidth;
    const clipped = [...document.querySelectorAll('main button, main input, main select, main [role="progressbar"], header button, header a')].map((element) => {
      const bounds = element.getBoundingClientRect();
      return { text: element.getAttribute('aria-label') || element.textContent.trim().slice(0, 80), left: bounds.left, right: bounds.right, width: bounds.width };
    }).filter((element) => element.width > 0 && (element.left < -1 || element.right > viewport + 1));
    return { viewport, documentWidth: document.documentElement.scrollWidth, bodyWidth: document.body.scrollWidth, clipped };
  });
  run.layout.push({ name, ...metrics });
  const filename = `${run.name}-${name}.png`;
  await page.screenshot({ path: path.join(evidenceDir, filename), fullPage: true, animations: 'disabled' });
  report.screenshots.push(filename);
  assert.ok(metrics.documentWidth <= metrics.viewport + 1, `${run.name}/${name}: document overflow ${JSON.stringify(metrics)}`);
  assert.deepEqual(metrics.clipped, [], `${run.name}/${name}: controls outside viewport`);
}

async function chooseTask(page, type) {
  await page.getByLabel('执行功能').selectOption(type);
  const course = page.getByRole('button', { name: /高等数学 · 第一学期/ });
  if (await course.getAttribute('aria-pressed') !== 'true') await course.click();
}

async function runViewport(name, viewport) {
  fixture = { tasks: new Map(), calls: [], serverErrors: [], failVideoStart: true, openAttempts: 0, opened: [] };
  const run = { name, viewport, layout: [], pageErrors: [], blockedRequests: [], passed: false };
  report.viewports.push(run);
  const context = await browser.newContext({ viewport, reducedMotion: 'reduce', locale: 'zh-CN' });
  try {
    await context.route('**/*', (route) => {
      if (route.request().url().startsWith(`${origin}/`)) return route.continue();
      run.blockedRequests.push(route.request().url());
      return route.abort();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror', (error) => run.pageErrors.push(error.message));
    await page.goto(origin, { waitUntil: 'networkidle' });
    await page.getByLabel('手机号').fill('fixture-account');
    await page.getByLabel('密码').fill('fixture-only-password');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByLabel('执行功能').waitFor();
    assert.equal(await page.getByRole('button', { name: '开始学习', exact: true }).isDisabled(), true);

    await chooseTask(page, 'visits');
    await page.getByLabel('每门课程提交次数').fill('0');
    assert.equal(await page.getByRole('button', { name: '开始提交次数' }).isDisabled(), true);
    await snapshot(page, run, 'visits-validation');
    await page.getByLabel('每门课程提交次数').fill('2');
    await page.getByLabel('提交间隔（秒）').fill('1');
    await page.getByRole('button', { name: '开始提交次数' }).click();
    await page.getByRole('region', { name: '工具执行结果' }).waitFor();
    await snapshot(page, run, 'visits-results');
    await page.getByRole('button', { name: '返回课程选择', exact: true }).click();

    await chooseTask(page, 'video_time');
    await page.getByRole('button', { name: '读取视频列表' }).click();
    const video = page.getByRole('checkbox', { name: `选择资源 ${resources[0].name}`, exact: true });
    await video.waitFor();
    assert.equal(await video.isChecked(), false);
    assert.equal(await page.getByRole('button', { name: '开始累计时长' }).isDisabled(), true);
    assert.equal(await page.getByRole('checkbox', { name: `选择资源 ${resources[4].name}`, exact: true }).isDisabled(), true);
    await video.check();
    await page.getByLabel('每个视频增加时长（分钟）').fill('0');
    assert.equal(await page.getByRole('button', { name: '开始累计时长' }).isDisabled(), true);
    await snapshot(page, run, 'video-validation');
    await page.getByLabel('每个视频增加时长（分钟）').fill('0.1');
    await page.getByRole('button', { name: '开始累计时长' }).click();
    await page.getByText('模拟来源任务已过期，请重新读取资源列表后重试。', { exact: true }).waitFor();
    const startErrorRegion = page.getByRole('region', { name: '资源选择' }).getByRole('alert');
    assert.equal(await startErrorRegion.count(), 1);
    const buttonBounds = await page.getByRole('button', { name: '开始累计时长' }).boundingBox();
    const errorBounds = await startErrorRegion.boundingBox();
    assert.ok(errorBounds.y - buttonBounds.y - buttonBounds.height < 30, 'Start error is beside the resource action');
    await snapshot(page, run, 'video-start-error');
    assert.equal(await video.isChecked(), true);
    await page.getByRole('button', { name: '开始累计时长' }).click();
    await page.getByRole('progressbar', { name: resources[0].name, exact: true }).waitFor();
    await snapshot(page, run, 'video-running');
    await page.getByRole('button', { name: '停止任务', exact: true }).click();
    await page.getByRole('button', { name: '确认停止任务', exact: true }).click();
    await page.getByText('任务已手动停止', { exact: true }).waitFor();
    await snapshot(page, run, 'video-cancelled');
    await page.getByRole('button', { name: '返回课程选择', exact: true }).click();

    await chooseTask(page, 'download');
    await page.getByRole('button', { name: '读取资源列表' }).click();
    const document = page.getByRole('checkbox', { name: `选择资源 ${resources[2].name}`, exact: true });
    await document.waitFor();
    assert.equal(await document.isChecked(), false);
    assert.equal(await page.getByRole('button', { name: '下载所选资源' }).isDisabled(), true);
    await page.getByLabel('资源类型').selectOption('document');
    await document.check();
    await page.getByLabel('资源类型').selectOption('all');
    await page.getByLabel('搜索资源').fill('课程音频');
    await page.getByRole('button', { name: '全选当前列表', exact: true }).click();
    await page.getByLabel('搜索资源').fill('');
    assert.equal(await document.isChecked(), true);
    assert.equal(await page.getByRole('checkbox', { name: `选择资源 ${resources[3].name}`, exact: true }).isChecked(), true);
    await snapshot(page, run, 'download-catalog');
    await page.getByRole('button', { name: '下载所选资源' }).click();
    await page.getByText('模拟资源读取中断，未保留不完整文件，请重新读取后重试。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '打开下载目录', exact: true }).click();
    await page.getByText('打开下载目录失败：模拟文件管理器启动失败，请重试。', { exact: true }).waitFor();
    await snapshot(page, run, 'download-open-error');
    await page.getByRole('button', { name: '打开下载目录', exact: true }).click();
    await page.getByText('已打开下载目录', { exact: true }).waitFor();
    await snapshot(page, run, 'download-result');
    assert.equal(fixture.openAttempts, 2);
    assert.equal(fixture.opened.length, 1);
    assert.deepEqual(run.pageErrors, []);
    assert.deepEqual(run.blockedRequests, []);
    assert.deepEqual(fixture.serverErrors, []);
    run.startTypes = fixture.calls.filter((call) => call.pathname === '/api/start').map((call) => call.payload.task_type);
    assert.deepEqual(run.startTypes, ['visits', 'catalog', 'video_time', 'video_time', 'catalog', 'download']);
    run.apiCalls = fixture.calls.length;
    run.passed = true;
  } finally {
    await context.close();
    run.contextClosed = true;
  }
}

(async () => {
  try {
    await startServer();
    browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
    await runViewport('desktop', { width: 1366, height: 900 });
    await runViewport('mobile', { width: 390, height: 844 });
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.error = error.stack;
    process.exitCode = 1;
  } finally {
    if (browser) {
      try { await browser.close(); report.cleanup.browserClosed = true; }
      catch (error) { report.cleanup.browserError = error.message; process.exitCode = 1; }
    }
    if (server) {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      report.cleanup.serverClosed = true;
    }
    report.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(evidenceDir, 'report.json'), JSON.stringify(report, null, 2));
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  }
})();
