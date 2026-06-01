export function buildPostgresUrl(
  host: string, port: string, name: string, username: string, password: string, extra: string
): string {
  let url = `postgresql+asyncpg://${encodeURIComponent(username)}`;
  if (password) url += `:${encodeURIComponent(password)}`;
  url += `@${host}`;
  if (port) url += `:${port}`;
  url += `/${name}`;
  if (extra) url += `${extra.startsWith('?') ? '' : '?'}${extra}`;
  return url;
}

export function parsePostgresUrl(url: string) {
  const defaults = { host: '', port: '5432', name: '', username: '', password: '', extra: '' };
  try {
    const u = new URL(url);
    defaults.host = u.hostname;
    defaults.port = u.port || '5432';
    defaults.name = u.pathname.replace(/^\//, '');
    defaults.username = decodeURIComponent(u.username);
    defaults.password = decodeURIComponent(u.password);
    defaults.extra = u.search.replace(/^\?/, '');
  } catch { /* ignore */ }
  return defaults;
}

export function buildRedisUrl(host: string, port: string, db: string, password: string): string {
  let url = 'redis://';
  if (password) url += `:${encodeURIComponent(password)}@`;
  url += `${host}`;
  if (port) url += `:${port}`;
  url += `/${db}`;
  return url;
}

export function parseRedisUrl(url: string) {
  const defaults = { host: '', port: '6379', db: '0', password: '' };
  try {
    const u = new URL(url);
    defaults.host = u.hostname;
    defaults.port = u.port || '6379';
    defaults.db = u.pathname.replace(/^\//, '') || '0';
    defaults.password = decodeURIComponent(u.password);
  } catch { /* ignore */ }
  return defaults;
}
