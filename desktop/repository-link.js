const REPOSITORY_URL = 'https://github.com/RRRRUDDDD/chaoxing-gui';

function repositoryWindowHandler(openExternal, onError) {
  return ({ url }) => {
    if (url === REPOSITORY_URL) {
      try { Promise.resolve(openExternal(REPOSITORY_URL)).catch(onError); }
      catch (error) { onError(error); }
    }
    return { action: 'deny' };
  };
}

module.exports = { REPOSITORY_URL, repositoryWindowHandler };
