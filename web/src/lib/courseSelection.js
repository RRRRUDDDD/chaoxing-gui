export function defaultSettings() {
  return {
    speed: 1.0, jobs: 1, notopen_action: 'retry', tiku_config: {},
    notification_config: { provider: 'Windows' }, ocr_config: {},
    shutdown_on_complete: false,
  };
}

export function restoreSettings(saved, aiEngine) {
  const settings = { ...defaultSettings(), ...saved };
  if (aiEngine && typeof aiEngine === 'object') {
    const tiku = { ...(settings.tiku_config || {}) };
    if (typeof aiEngine.enabled === 'boolean') tiku.ai_enabled = aiEngine.enabled;
    if (typeof aiEngine.priority === 'string' && aiEngine.priority) tiku.ai_priority = aiEngine.priority;
    settings.tiku_config = tiku;
  }
  if (typeof settings.notification_config?.provider !== 'string') {
    settings.notification_config = { ...settings.notification_config, provider: 'Windows' };
  }
  if (!['retry', 'continue'].includes(settings.notopen_action)) settings.notopen_action = 'retry';
  settings.shutdown_on_complete = settings.shutdown_on_complete === true;
  return settings;
}

export function normalizeCourses(courses) {
  if (!Array.isArray(courses)) throw new Error('课程列表格式错误，请重新加载');
  return courses.map((course) => ({ ...course, courseId: String(course.courseId) }));
}

export function restoreCourseSelection(config, username, courses) {
  const ids = courses.map((course) => String(course.courseId));
  const accounts = config?.selectedCoursesByAccount;
  const saved = !!accounts && Object.hasOwn(accounts, username);
  if (!saved) return { ids: [], saved: false };
  const valid = new Set(ids);
  const selection = Array.isArray(accounts[username]) ? accounts[username] : [];
  return { ids: [...new Set(selection.map(String))].filter((id) => valid.has(id)), saved: true };
}

// Compare the actual save payload, ignoring course click order and object key order.
export function configSnapshot(settings, username, selectedCourses) {
  const canonical = (value) => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.keys(value).sort().filter((key) => value[key] !== undefined)
        .map((key) => [key, canonical(value[key])]));
    }
    return value;
  };
  return JSON.stringify(canonical({
    settings,
    selectedCoursesByAccount: { [username]: [...new Set(selectedCourses.map(String))].sort() },
  }));
}

export function updateTikuSettings(settings, field, value) {
  const previous = settings.tiku_config || {};
  const next = {};
  for (const key of ['config', 'submit', 'cover_rate', 'delay', 'true_list', 'false_list', 'verify_ssl', 'wrappers', 'subscription', 'ai_enabled', 'ai_priority']) {
    if (previous[key] !== undefined) next[key] = previous[key];
  }
  return { ...settings, tiku_config: { ...next, [field]: value } };
}
