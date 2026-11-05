"""Apply local .gitignore rules using Git, without consulting a repository's index."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import tempfile

from .selection import includes
from .storage import Blocked


class Selection:
    def __init__(self, settings, sources, repository):
        self.settings, self.sources, self.repository = settings, sources, repository
        self.cache = {}
        # Ignore user/system Git configuration and inherited repository overrides.
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)

    def git(self, *arguments, **kwargs):
        try:
            result = subprocess.run(['git', *arguments], env=self.environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        except OSError as error:
            raise Blocked('Git is required to apply .gitignore rules. Install git before syncing.') from error
        if result.returncode not in (0, 1):
            raise Blocked('Could not evaluate local .gitignore rules. Check their permissions before syncing.')
        return result

    def prepare(self, entries):
        """Batch siblings before scanning, including directory hints for cloud-only folders."""
        groups = {}
        for path, directory in entries:
            key = (path, directory)
            name, _, rest = path.partition('/')
            if key in self.cache or not rest or not includes(path, self.settings):
                continue
            source = self.sources.get(name)
            if source is None or not source.is_dir():
                self.cache[key] = True
                continue
            current = source
            unsafe = source.is_symlink() or not os.access(source, os.R_OK | os.X_OK)
            for part in rest.split('/'):
                if unsafe:
                    break
                current = current / part
                unsafe = current.is_symlink() or (current.is_dir() and not os.access(current, os.R_OK | os.X_OK))
            if unsafe:
                # The scanner reports these paths as skipped on both sides.
                self.cache[key] = True
                continue
            groups.setdefault(name, []).append((key, rest + ('/' if directory else '')))
        for name, entries in groups.items():
            result = self.git('--git-dir=' + str(self.repository),
                              '--work-tree=' + str(self.sources[name]),
                              '-c', 'core.excludesFile=' + os.devnull,
                              'check-ignore', '--no-index', '-z', '--stdin',
                              input=b''.join(os.fsencode(rest) + b'\0' for _, rest in entries))
            ignored = set(result.stdout.split(b'\0'))
            for key, rest in entries:
                self.cache[key] = os.fsencode(rest) not in ignored

    def __call__(self, path, directory=False):
        if not includes(path, self.settings):
            return False
        self.prepare([(path, directory)])
        return self.cache.get((path, directory), True)


@contextmanager
def selection(settings, sources):
    with tempfile.TemporaryDirectory(prefix='omarchy-drive-ignore-') as temporary:
        matcher = Selection(settings, sources, Path(temporary) / 'git')
        matcher.git('init', '--bare', '--quiet', '--template=', str(matcher.repository))
        yield matcher
