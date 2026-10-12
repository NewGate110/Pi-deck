/* Maintenance layout; existing actions and confirmations are retained. */
(() => {
  const original = openBotManagement;
  openBotManagement = async function (bot) {
    await original(bot);
    if (managementPanel !== 'bot' || managementBot?.id !== bot.id) return;
    const content = document.getElementById('management-content');
    const actions = content.querySelector('.maintenance-actions');
    if (!actions) return;
    const section = actions.closest('details');
    section.classList.add('xp-maintenance');
    const review = section.querySelector('[data-review-git]');
    const warning = section.querySelector('.notice');
    const buttons = Object.fromEntries([...actions.querySelectorAll('[data-maintenance]')].map(b => [b.dataset.maintenance, b]));
    function group(title, description, controls) {
      const row = document.createElement('div');
      row.className = 'xp-maintenance-row';
      const text = document.createElement('div');
      const heading = document.createElement('h3');
      heading.textContent = title;
      const help = document.createElement('p');
      help.textContent = description;
      text.append(heading, help);
      const toolbar = document.createElement('div');
      toolbar.className = 'xp-maintenance-buttons';
      toolbar.append(...controls.filter(Boolean));
      row.append(text, toolbar);
      return row;
    }
    actions.replaceWith(
      group('Update code', 'Check for new commits, then choose how to apply them.', [buttons.check, buttons.pull, buttons.update]),
      group('Dependencies', 'Install packages from requirements.txt when needed.', [buttons.dependencies])
    );
    buttons.check.classList.replace('secondary', 'primary');
    const recovery = document.createElement('details');
    recovery.className = 'xp-maintenance-advanced';
    const summary = document.createElement('summary');
    summary.textContent = 'Advanced & recovery';
    recovery.append(summary);
    recovery.append(group('Review local changes', 'Inspect the diff before considering a force update.', [review]));
    recovery.append(group('Recover a version', 'Restore earlier code or return to the saved branch.', [buttons.rollback, buttons.resume]));
    if (warning) recovery.append(warning);
    section.append(recovery);
  };
})();
