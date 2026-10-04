// 组件文案与可访问性角色的单一事实来源：组件渲染、vitest 断言与
// desktop/scripts/p3-smoke.mjs（发布冒烟）三方共享。这里的键被发布冒烟
// 脚本按选择器消费，改名或改值前先同步 p3-smoke.mjs 的用法。
export const loginLabels = {
  phone: '手机号',
  password: '密码',
  submit: '登录',
};

export const recheckLabel = '重新检查';
export const startStudyLabel = '开始学习';
export const saveDefaultLabel = '保存为默认配置';
export const configSavedMessage = '配置已保存';
export const expiredTaskMessage = '上次任务已过期或没有可恢复的记录，请重新选择课程。';
export const logRole = 'log';
