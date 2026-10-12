/* Interactive Pi Deck command console. Never executes shell commands. */
(() => {
  const dialog = document.getElementById('xp-terminal');
  const output = document.getElementById('xp-terminal-output');
  const form = document.getElementById('xp-terminal-form');
  const input = document.getElementById('xp-terminal-input');
  const history = [];
  let cursor = 0, draft = '', running = false;
  function write(text, kind = '') {
    const line = document.createElement('div');
    line.className = kind;
    line.textContent = text;
    output.append(line);
    while (output.children.length > 300) output.firstElementChild.remove();
    output.scrollTop = output.scrollHeight;
  }
  function welcome() {
    write('Pi Deck Command Console' + (demo ? ' [DEMO]' : ''), 'terminal-info');
    write(demo ? 'Sample bots only. This is a command interface, not a system shell.' : 'Authenticated Pi Deck commands. Actions affect the registered services.');
    write('Type help for commands. Use ↑ / ↓ for command history.\n');
  }
  document.getElementById('open-terminal').onclick = () => {
    document.getElementById('terminal-session-label').textContent = demo ? 'DEMO SESSION · Sample bots only' : 'LIVE SESSION · Registered services';
    if (!output.children.length) welcome();
    dialog.showModal();
    input.focus();
  };
  document.getElementById('close-terminal').onclick = () => dialog.close();
  input.addEventListener('keydown', event => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    if (cursor === history.length) draft = input.value;
    cursor = Math.max(0, Math.min(history.length, cursor + (event.key === 'ArrowUp' ? -1 : 1)));
    input.value = cursor === history.length ? draft : history[cursor];
    input.setSelectionRange(input.value.length, input.value.length);
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const command = input.value.trim();
    if (!command || running) return;
    history.push(command);
    if (history.length > 100) history.shift();
    cursor = history.length;
    draft = '';
    input.value = '';
    write('pi-deck> ' + command, 'terminal-command');
    running = true;
    input.disabled = true;
    form.querySelector('button').disabled = true;
    try {
      const [verb, ...args] = command.split(/\s+/);
      const cmd = verb.toLowerCase();
      if (cmd === 'help' && !args.length) {
        write('help                 Show available commands\nbots                 List bot IDs and service states\nstatus <bot-id>      Show service and startup status\njobs                 Show recent background jobs\nstart <bot-id>       Start a bot\nstop <bot-id>        Stop a bot (confirmation required)\nrestart <bot-id>     Restart a bot (confirmation required)\nclear                Clear this console\nexit                 Close this window\n\nNo shell commands, pipes, or file access are supported.');
      } else if (cmd === 'clear' && !args.length) {
        output.replaceChildren(); welcome();
      } else if (cmd === 'exit' && !args.length) {
        dialog.close();
      } else if (cmd === 'jobs' && !args.length) {
        const data = await api('jobs');
        write(data.jobs.length ? data.jobs.map(j => `[${j.state}] ${j.label} ${j.bot || ''}\n  ${dateText(j.time)} · ${j.message || ''}`).join('\n') : 'No background jobs yet.');
      } else if (cmd === 'bots' && !args.length) {
        const data = await api('bots');
        write(data.bots.length ? data.bots.map(b => `${b.id.padEnd(24)} ${b.state.padEnd(10)} ${b.name}`).join('\n') : 'No bots configured.');
      } else if (['status','start','stop','restart'].includes(cmd) && args.length === 1) {
        const data = await api('bots');
        const bot = data.bots.find(b => b.id === args[0]);
        if (!bot) throw new Error('Unknown bot ID. Type bots to see available IDs.');
        if (cmd === 'status') {
          write(`${bot.name}\nID:       ${bot.id}\nService:  ${bot.service}\nState:    ${bot.state}\nAt boot:  ${bot.enabled ? 'enabled' : 'disabled'}`);
        } else {
          if (cmd !== 'start' && !await confirmAction(`${cmd === 'stop' ? 'Stop' : 'Restart'} ${bot.name}?`, (demo ? 'Demo only: simulate this service action. ' : 'This changes the running service on your Pi. ') + 'Stopping a bot does not change its start-on-boot setting.')) {
            write('Cancelled.'); return;
          }
          const result = await api(`bots/${encodeURIComponent(bot.id)}/action`, 'POST', {action:cmd, background:true});
          write(result.message || 'Action queued. Type jobs to check its result.', 'terminal-success');
          await refresh();
        }
      } else {
        throw new Error('Unknown command or incorrect arguments. Type help for usage.');
      }
    } catch (error) {
      write('Error: ' + error.message, 'terminal-error');
    } finally {
      running = false;
      input.disabled = false;
      form.querySelector('button').disabled = false;
      if (dialog.open) input.focus();
    }
  });
})();
