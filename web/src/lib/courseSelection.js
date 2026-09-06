export function defaultSettings() {
  return {
    speed: 1.0, jobs: 1, notopen_action: 'retry', tiku_config: {},
    notification_config: { provider: 'Windows' }, ocr_config: {},
  };
}

export function restoreSettings(saved) {
  const settings = { ...defaultSettings(), ...saved };
  if (typeof settings.notification_config?.provider !== 'string') {
    settings.notification_config = { ...settings.notification_config, provider: 'Windows' };
  }
  if (!['retry', 'continue'].includes(settings.notopen_action)) settings.notopen_action = 'retry';
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
  if (!saved) return { ids, saved: false };
  const valid = new Set(ids);
  const selection = Array.isArray(accounts[username]) ? accounts[username] : [];
  return { ids: [...new Set(selection.map(String))].filter((id) => valid.has(id)), saved: true };
}
