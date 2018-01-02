import io
import os
import re
import warnings

import setupmeta
from setupmeta.scm import Git, Version


BUMPABLE = 'major minor patch'.split()
RE_VERSIONING = re.compile(r'^(tag(\([\w\s,\-]+\))?:)?(.*?)([ +@#%^;/,]!?(.*))?$')

DEFAULT_SEPARATOR = '+'
DEFAULT_MAIN = '{major}.{minor}.{patch}{post}'
CHANGES_MAIN = '{major}.{minor}.{changes}'
DEFAULT_EXTRA = '{commitid}'
DEFAULT_BRANCHES = 'master'


def has_scm_mark(root, name):
    return os.path.isdir(os.path.join(root, '.%s' % name))


def project_scm(root):
    """
    :param str root: Path to project folder
    :return setupmeta.scm.Scm: SCM used by project, if any
    """
    if has_scm_mark(root, 'git'):
        return Git(root)
    setupmeta.trace("could not determine SCM for '%s'" % root)
    return None


class VersionBit:
    def __init__(self, strategy, text, alternative=None, constant=False):
        self.strategy = strategy
        self.text = text
        self.alternative = alternative
        self.constant = constant
        self.renderer = None
        self.problem = None
        if self.constant:
            self.renderer = self.rendered_constant
        elif '$' in self.text:
            self.renderer = self.rendered_env_var
        elif not hasattr(Version, self.text):
            self.problem = "invalid versioning part '%s'" % self.text
        else:
            self.renderer = self.rendered_attr

    def __repr__(self):
        text = self.text
        if self.alternative:
            text = '%s:%s' % (text, self.alternative)
        if self.constant:
            text = "'%s'" % text
        else:
            text = '{%s}' % text
        if self.problem:
            text = " [%s]" % self.problem
        return text

    def rendered_attr(self, version):
        """
        :param Version version: Version to render
        :return str: Rendered version bit
        """
        return getattr(version, self.text, None)

    def rendered_constant(self, version):
        """
        :param Version version: Version to render
        :return str: Rendered version bit
        """
        return self.text

    def rendered_env_var(self, version):
        """
        :param Version version: Version to render
        :return str: Rendered version bit
        """
        i = self.text.index('$')
        prefix = self.text[:i]
        env_var = self.text[i + 1:]
        if env_var.startswith('*') and env_var.endswith('*'):
            env_var = env_var[1:-1]
            candidates = [n for n in os.environ if env_var in n]
        elif env_var.startswith('*'):
            env_var = env_var[1:]
            candidates = [n for n in os.environ if n.endswith(env_var)]
        elif env_var.endswith('*'):
            env_var = env_var[:-1]
            candidates = [n for n in os.environ if n.startswith(env_var)]
        else:
            candidates = [env_var]
        value = None
        if candidates:
            value = os.environ.get(sorted(candidates)[0])
        if value is None:
            value = self.alternative
        if value is None:
            if prefix:
                return ''
            return None
        return "%s%s" % (prefix, value)

    def rendered(self, version):
        """
        :param Version version: Version to render
        :return str: Rendered version bit
        """
        if not self.renderer:
            return 'invalid'
        value = self.renderer(version)
        return str(value)


class Strategy:

    def __init__(self, main, extra, separator, branches, **kwargs):
        self.main = main
        self.extra = extra
        if kwargs:
            warnings.warn("Ignored fields for 'versioning': %s" % kwargs)
        self.main_bits = self.bits(main)
        self.extra_bits = self.bits(extra)
        self.separator = separator or DEFAULT_SEPARATOR
        self.branches = branches or DEFAULT_BRANCHES
        if self.branches and hasattr(self.branches, 'lstrip'):
            self.branches = self.branches.lstrip('(').rstrip(')')
        self.branches = setupmeta.listify(self.branches, separator=',')
        self.text = self.formatted(self.branches, self.main, self.separator, self.extra)
        if not self.main_bits:
            self.problem = "No versioning format specified"
            return
        all_bits = self.main_bits if isinstance(self.main_bits, list) else []
        if isinstance(self.extra_bits, list):
            all_bits = all_bits + self.extra_bits
        problems = [bit.problem for bit in all_bits if bit.problem]
        self.problem = '\n'.join(problems) if problems else None

    @staticmethod
    def formatted(branches, main, separator, extra):
        if isinstance(branches, list):
            branches = ','.join(branches)
        result = ''
        if main:
            result += str(main)
        if result or extra:
            result += separator
        if extra:
            result += str(extra)
        if branches:
            result = 'tag(%s):%s' % (branches, result)
        return result

    def bits(self, fmt):
        if callable(fmt):
            return fmt
        elif fmt and fmt[0] == '!':
            fmt = fmt[1:]
        result = []
        if not fmt:
            return result
        before, _, after = fmt.partition('{')
        if before:
            result.append(VersionBit(self, before, constant=True))
        if not after:
            return result
        part, _, rest = after.partition('}')
        if ':' in part:
            left, _, right = part.partition(':')
            left = VersionBit(self, left, alternative=right)
            result.append(left)
        else:
            part = VersionBit(self, part)
            result.append(part)
        result.extend(self.bits(rest))
        return result

    def __repr__(self):
        return self.text

    def needs_extra(self, version):
        if not self.extra:
            return False
        if not isinstance(self.extra_bits, list):
            return True
        return self.extra[0] == '!' or version.dirty

    def rendered(self, version, extra=True):
        """
        :param Version version: Version to render
        :param bool extra: Render extra part?
        :return str: Rendered version
        """
        result = self.rendered_bits(version, self.main_bits) or []
        if extra and self.needs_extra(version):
            extra = self.rendered_bits(version, self.extra_bits)
            if extra:
                if self.separator != ' ':
                    result.append(self.separator)
                result.extend(extra)
        return ''.join(result)

    @staticmethod
    def rendered_bits(version, bits):
        if isinstance(bits, list):
            return [bit.rendered(version) for bit in bits]
        if callable(bits):
            value = bits(version)
            if value:
                return [value]
        return None

    def bumped(self, what, current_version):
        """
        :param str what: Which component to bump
        :param Version current_version: Current version
        :return str: Represented next version, with 'what' bumped
        """
        if not isinstance(self.main_bits, list):
            setupmeta.abort("Main format is not a list: %s" % setupmeta.stringify(self.main_bits))

        bumpable = [b.text for b in self.main_bits if b.text in BUMPABLE]
        if what not in bumpable:
            msg = "Can't bump '%s', it's out of scope" % what
            msg += " of main format '%s'" % self.main_bits
            setupmeta.abort(msg)

        major, minor, rev = current_version.bump_triplet()
        if what == 'major':
            major, minor, rev = (major + 1, 0, 0)
        elif what == 'minor':
            major, minor, rev = (major, minor + 1, 0)
        elif what == 'patch':
            major, minor, rev = (major, minor, rev + 1)

        next_version = Version(main="%s.%s.%s" % (major, minor, rev))
        return self.rendered(next_version, extra=False)

    @classmethod
    def from_meta(cls, given):
        if not given:
            return None

        data = dict(main=DEFAULT_MAIN, extra=DEFAULT_EXTRA, separator=DEFAULT_SEPARATOR, branches=DEFAULT_BRANCHES)

        if isinstance(given, dict):
            data.update(given)

        elif given == 'changes':
            data['main'] = CHANGES_MAIN

        elif given != 'tag' and given is not True:
            m = RE_VERSIONING.match(given)
            if m.group(2):
                data['branches'] = m.group(2)
            data['main'] = m.group(3)
            extra = m.group(4)
            if extra:
                data['separator'] = extra[0]
                data['extra'] = extra[1:]

        return cls(**data)


class Versioning:
    def __init__(self, meta, scm):
        """
        :param setupmeta.model.SetupMeta meta: Parent meta object
        :param Scm scm: Backend SCM
        """
        self.meta = meta
        given = meta.value('versioning')
        self.strategy = Strategy.from_meta(given)
        self.enabled = bool(given and self.strategy and not self.strategy.problem)
        self.scm = scm
        if not self.strategy:
            self.problem = "setupmeta versioning not enabled"
        elif not self.scm:
            self.problem = "project not under a supported SCM"
        else:
            self.problem = self.strategy.problem
        setupmeta.trace("versioning given: '%s', strategy: [%s], problem: [%s]" % (given, self.strategy, self.problem))

    @staticmethod
    def formatted(main=DEFAULT_MAIN, extra=DEFAULT_EXTRA, separator=DEFAULT_SEPARATOR, branches=DEFAULT_BRANCHES):
        return Strategy.formatted(branches, main, separator, extra)

    def auto_fill_version(self):
        """
        Auto-fill version as defined by self.strategy
        :param setupmeta.model.SetupMeta meta: Parent meta object
        """
        if not self.enabled:
            setupmeta.trace("not auto-filling version, versioning is disabled")
            return
        vdef = self.meta.definitions.get('version')
        cv = vdef.sources[0].value if vdef and vdef.sources else None
        if cv and vdef and vdef.source == 'pygradle':
            setupmeta.trace("not auto-filling version for pygradle")
            return
        if self.problem:
            if not cv:
                self.meta.auto_fill('version', '0.0.0', 'missing')
            if self.strategy:
                warnings.warn(self.problem)
            setupmeta.trace("not auto-filling version due to problem: [%s]" % self.problem)
            return

        gv = self.scm.get_version()
        rendered = self.strategy.rendered(gv)
        if cv and rendered and not rendered.startswith(cv):
            source = vdef.sources[0].source
            expected = rendered[:len(cv)]
            msg = "In %s version should be %s, not %s" % (source, expected, cv)
            warnings.warn(msg)
        self.meta.auto_fill('version', rendered, 'git', override=True)

    def bump(self, what, commit=False, commit_all=False, simulate_branch=None):
        if self.problem:
            setupmeta.abort(self.problem)

        branch = simulate_branch or self.scm.get_branch()
        if branch not in self.strategy.branches:
            setupmeta.abort("Can't bump branch '%s', need one of %s" % (branch, self.strategy.branches))

        gv = self.scm.get_version()
        if commit and gv and gv.dirty and not commit_all:
            setupmeta.abort("You have pending git changes, can't bump")

        next_version = self.strategy.bumped(what, gv)

        if not commit:
            print("Not committing bump, use --commit to commit")

        vdefs = self.meta.definitions.get('version')
        if vdefs:
            self.update_sources(next_version, commit, commit_all, vdefs)

        self.scm.apply_tag(commit, next_version)

        hook = setupmeta.project_path('bump-hook')
        if not setupmeta.is_executable(hook):
            return

        setupmeta.run_program(hook, fatal=True, dryrun=not commit, cwd=setupmeta.project_path())

    def update_sources(self, next_version, commit, commit_all, vdefs):
        modified = []
        for vdef in vdefs.sources:
            if '.py:' not in vdef.source:
                continue

            relative_path, _, target_line = vdef.source.partition(':')
            full_path = setupmeta.project_path(relative_path)
            target_line = setupmeta.to_int(target_line, default=0)

            lines = []
            changed = 0
            line_number = 0
            revised = None
            with io.open(full_path, 'rt', encoding='utf-8') as fh:
                for line in fh.readlines():
                    line_number += 1
                    if line_number == target_line:
                        revised = updated_line(line, next_version, vdef)
                        if revised and revised != line:
                            changed += 1
                            line = revised
                    lines.append(line)

            if not changed:
                print("%s already has the right version" % vdef.source)

            else:
                modified.append(relative_path)
                if commit:
                    with io.open(full_path, 'wt', encoding='utf-8') as fh:
                        fh.writelines(lines)
                else:
                    print("Would update %s with '%s'" % (vdef.source, revised.strip()))

        if not modified:
            return

        if commit_all:
            modified = ['.']
        self.scm.commit_files(commit, modified, next_version)


def updated_line(line, next_version, vdef):
    if '=' in line:
        sep = '='
        next_version = "'%s'" % next_version
    else:
        sep = ':'

    key, _, value = line.partition(sep)
    space = ' ' if value and value[0] == ' ' else ''
    return "%s%s%s%s\n" % (key, sep, space, next_version)
