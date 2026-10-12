/* XP job window presentation. */
(() => {
  const originalShowManagement = showManagement;
  showManagement = function (title, panel, bot = null) {
    const dialog = document.getElementById('management-dialog');
    dialog.classList.toggle('xp-job-console', panel === 'jobs');
    dialog.dataset.consoleMode = demo ? 'demo' : 'live';
    return originalShowManagement(
      panel === 'jobs' ? 'Background jobs — Command Prompt' : title, panel, bot
    );
  };
})();
