"""Review a pinned upstream commit and preserve recovery refs before replacement."""
import hashlib
import json
from pathlib import Path
import secrets
from management import progress


def review(bot, git, fetch=True):
    if fetch:
        git(bot, 'fetch', '--prune')
    branch = git(bot, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    upstream = git(bot, 'rev-parse', '--abbrev-ref', '@{upstream}')
    head = git(bot, 'rev-parse', 'HEAD')
    target = git(bot, 'rev-parse', '@{upstream}')
    ahead, behind = map(int, git(bot, 'rev-list', '--left-right', '--count', 'HEAD...@{upstream}').split())
    options = ('--no-ext-diff', '--no-textconv', '--no-color', '--no-renames')
    # Binary patches are fingerprinted too, even though the UI only shows a notice.
    staged = git(bot, 'diff', *options, '--binary', '--cached', 'HEAD', '--')
    unstaged = git(bot, 'diff', *options, '--binary', '--')
    incoming = git(bot, 'diff', *options, '--binary', head, target, '--')
    local = git(bot, 'diff', *options, '--binary', target+'...'+head, '--') if ahead else ''
    status = git(bot, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    target_entries = git(bot, 'ls-tree', '-r', '-z', target).split('\0')
    index_entries = git(bot, 'ls-files', '--stage', '-z').split('\0')
    target_files = {entry.split('\t', 1)[1] for entry in target_entries if '\t' in entry}
    index_files = {entry.split('\t', 1)[1] for entry in index_entries if '\t' in entry}
    untracked = [p for p in git(bot, 'ls-files', '--others', '--directory', '-z').split('\0') if p]
    blockers = []
    flags = git(bot, 'ls-files', '-v', '-z').split('\0')
    if any(entry and (entry[0].islower() or entry[0] == 'S') for entry in flags):
        blockers.append('Git is hiding changes with assume-unchanged or skip-worktree flags. Clear those flags and review on the Pi.')
    if any(entry.startswith('160000 ') for entry in target_entries+index_entries):
        blockers.append('This repository uses submodules. Update it manually on the Pi.')
    if git(bot, 'ls-files', '--unmerged'):
        blockers.append('Resolve the existing merge conflicts on the Pi first.')
    git_dir = Path(git(bot, 'rev-parse', '--absolute-git-dir'))
    if any((git_dir/name).exists() for name in ('MERGE_HEAD','rebase-merge','rebase-apply','CHERRY_PICK_HEAD','REVERT_HEAD','sequencer')):
        blockers.append('A Git operation is in progress. Finish or abort it on the Pi first.')
    root = Path(git(bot, 'rev-parse', '--show-toplevel')).resolve()
    if root != Path(bot['repo']).resolve():
        blockers.append('The registered directory must be the root of the Git checkout.')
    protected = {p for p in target_files|index_files if Path(p).name == '.env' or Path(p).name.startswith('.env.')}
    if bot.get('env'):
        try: protected.add(Path(bot['env']).resolve().relative_to(root).as_posix())
        except ValueError: pass
    if protected & (target_files|index_files):
        blockers.append('An environment file is tracked by Git. Resolve it manually to avoid replacing configuration.')
    collisions = []
    for path in untracked:
        path = path.rstrip('/')
        if any(path == remote or path.startswith(remote+'/') or remote.startswith(path+'/') for remote in target_files):
            collisions.append(path)
    if collisions:
        blockers.append('Untracked or ignored files would be overwritten: '+', '.join(collisions[:10]))
    sections = [dict(title=title, patch=patch) for title, patch in (
        ('Staged changes on the Pi', staged), ('Unstaged changes on the Pi', unstaged),
        ('Local commits that will be replaced', local), ('Incoming changes from upstream', incoming))]
    if any('GIT binary patch' in section['patch'] for section in sections):
        blockers.append('Binary files changed and cannot be reviewed as text here. Update them manually on the Pi.')
    if sum(len(section['patch'].splitlines()) for section in sections)>4000:
        blockers.append('More than 4,000 diff lines. Review and update this repository on the Pi.')
    fingerprint = dict(bot=bot, branch=branch, head=head, target=target, upstream=upstream,
                       staged=staged, unstaged=unstaged, local=local, status=status, untracked=untracked)
    revision = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    budget = 250000
    for section in sections:
        patch = section['patch']
        if len(patch) > budget:
            section['patch'] = patch[:budget]
            blockers.append('The diff is too large for a complete preview. Review and update it on the Pi.')
        budget = max(0, budget-len(section['patch']))
    return dict(branch=branch, upstream=upstream, head=head, target=target, ahead=ahead, behind=behind,
                revision=revision, sections=sections, untracked=untracked[:200], untracked_count=len(untracked),
                blockers=list(dict.fromkeys(blockers)), can_force=not blockers)


def force_update(bot, git, expected, save_recovery):
    progress('Rechecking reviewed local changes and upstream commit')
    current = review(bot, git)
    if current['revision'] != expected:
        raise ValueError('Repository or upstream changed since the preview. Review the changes again.')
    if not current['can_force']:
        raise ValueError('Force update blocked: '+' '.join(current['blockers']))
    ref = 'refs/pi-deck/recovery/'+secrets.token_hex(12)
    progress('Saving tracked changes and current commit to a local recovery ref')
    snapshot = git(bot, '-c', 'user.name=Pi Deck', '-c', 'user.email=pi-deck@localhost',
                   'stash', 'create', 'Pi Deck before force update')
    git(bot, 'update-ref', ref, snapshot or current['head'])
    recovery = dict(ref=ref, stash=bool(snapshot), head=current['head'], target=current['target'],
                    branch=current['branch'], repo=bot['repo'])
    save_recovery(recovery)  # Persist the recovery pointer before any destructive operation.
    # Refuse edits that arrived while the recovery copy was being created.
    recheck = review(bot, git, fetch=False)
    if recheck['revision'] != expected or not recheck['can_force']:
        raise ValueError('Repository changed while creating the recovery copy. Review again; no reset was performed.')
    progress('Replacing tracked code with the reviewed upstream commit; no service restart')
    git(bot, 'reset', '--hard', current['target'])
    return dict(message='Force update complete. Restart separately when ready. Recovery copy: '+ref,
                recovery=recovery, current=current['target'][:12], branch=current['branch'])
