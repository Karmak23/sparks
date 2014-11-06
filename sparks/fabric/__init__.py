# -*- coding: utf8 -*-
from __future__ import print_function

import sys
import os
import re
import ast
import string
import random
import logging
import platform
import functools
try:
    import cPickle as pickle
except:
    import pickle

try:
    import cStringIO as StringIO
except:
    import StringIO

from ..foundations.classes import SimpleObject

from . import nofabric
# Cannot import "utils" here, circular loop.

try:
    from fabric.api              import env, execute, task
    from fabric.api              import run as fabric_run
    from fabric.api              import sudo as fabric_sudo
    from fabric.api              import local as fabric_local
    from fabric.operations       import get, put
    from fabric.context_managers import prefix, cd, lcd, hide
    from fabric.colors           import cyan

    # imported from utils
    from fabric.contrib.files    import exists # NOQA

    # used in sparks submodules, not directly here. Thus the # NOQA.
    from fabric.api              import task # NOQA
    from fabric.context_managers import cd # NOQA
    from fabric.colors           import green # NOQA

    #if not env.all_hosts:
    #    env.host_string = 'localhost'

    _wrap_fabric = False

    #
    # NOTE: we re-wrap fabric functions into ours, which test against localhost.
    #   This allows multiprocessing to run correctly on localhost, via real
    #   local commands.
    #   Else, paramiko will fail with obscure errors (observed in the `pkgmgr`),
    #   and it's horribly terrible to debug (trust me).
    #   Hoping nobody will try to multiprocessing.Process(fabric) via sparks
    #   (or not). This only seems to work when fabric does the @parallel jobs,
    #   not when trying to run fabric tasks in multiprocessing.* encapsulation.
    #

    def run(*args, **kwargs):
        if is_localhost(env.host_string):
            return nofabric.run(*args, **kwargs)

        else:
            return fabric_run(*args, **kwargs)

    def local(*args, **kwargs):
        if is_localhost(env.host_string):
            return nofabric.local(*args, **kwargs)

        else:
            return fabric_local(*args, **kwargs)

    def sudo(*args, **kwargs):
        if is_localhost(env.host_string):
            return nofabric.sudo(*args, **kwargs)

        else:
            return fabric_sudo(*args, **kwargs)

except ImportError:
    # If fabric is not available, this means we are imported from 1nstall.py,
    # or more generaly fabric is not installed.
    # Everything will fail except the base system detection. We define the bare
    # minimum for it to work on a local Linux/OSX system.

    run    = nofabric.run # NOQA
    local  = nofabric.local # NOQA
    sudo   = nofabric.sudo # NOQA
    exists = nofabric.exists # NOQA


# Global way to turn all of this module silent.
DEBUG = bool(os.environ.get('SPARKS_DEBUG', False))
QUIET = not DEBUG and not bool(os.environ.get('SPARKS_VERBOSE', False))
LOGGER = logging.getLogger(__name__)
remote_configuration = None
local_configuration  = None

all_roles = [
    'web', 'proxy',
    'db', 'memcache', 'mysql',
    'pg', 'mongodb', 'redis',
    'load', 'ha', 'loadbalancer',
    'monitoring', 'stats',
    'lang', 'admin',
    'beat', 'flower', 'shell',
]

worker_information = {}

for worker_type, worker_name in (
    ('worker',            'Generic'),
    ('worker_io',         'I/O dedicated'),
    ('worker_net',        'Network'),
    ('worker_solo',       'Mono-process'),
    ('worker_duo',        'Dual-process'),
    ('worker_trio',       'Tri-process'),
    ('worker_default',    'Default'),
    ('worker_swarm',      'Swarm-processing'),
    ('worker_check',      'Checker'),
    ('worker_create',     'Create'),
    ('worker_refresh',    'Refresher'),
    ('worker_fetch',      'Fetcher'),
    ('worker_index',      'Indexer'),
    ('worker_crawl',      'Crawler'),
    ('worker_archive',    'Archive'),
    ('worker_clean',      'Cleaner'),
    ('worker_backup',     'Backup'),
    ('worker_background', 'Background'),
    ('worker_client',     'Client'),
    ('worker_server',     'Server'),
    ('worker_social',     'Social'),
    ('worker_partners',   'Partner'),
    ('worker_users',      'User processes'),
    ('worker_system',     'System'),
        ('worker_cluster',  'Cluster')):
    for sub_name, priority in (
        (worker_type,               ''),
        (worker_type + '_low',      'Low-priority '),
        (worker_type + '_medium',   'Medium-priority '),
            (worker_type + '_high', 'High-priority ')):
        all_roles.append(sub_name)

        worker_information.update({
            sub_name: (priority + worker_name + ' worker',
                       sub_name[7:] or 'celery')
        })

worker_roles = [r for r in all_roles if r.startswith('worker')]


# ===================================================== Fabric helper functions

def execute_or_not(task, *args, **kwargs):
    """ Run Fabric's execute(), but only if there are hosts/roles to run it on.
        Else, just discard the task, and print a warning message.

        This allows to have empty roles/hosts lists for some tasks, in
        architectures where all roles are not needed.

        .. note:: if you would like to have many services on the same host
            (eg. a worker_high and a worker_low, with 2 different
            configurations), you should call execute_or_not() once at a
            time for each role, not one time with all roles grouped in a
            list parameter. See `sparks.django.fabfile.restart_services()`
            for an example. **This is a limitation of the sparks model**.

        .. versionadded: 2.x.
    """

    # execute kwargs: host, hosts, role, roles and exclude_hosts

    roles = kwargs.pop('sparks_roles', ['__any__'])
    non_empty = [role for role in roles if env.roledefs.get(role, []) != []]

    #LOGGER.warning('roledefs: %s', env.roledefs)
    #LOGGER.warning('roles: %s, current_context:%s, matching: %s',
    #               roles, non_empty, env.roledefs.get(roles[0], []))

    # Reset in case. The role should be found preferably in
    # env.host_string.role, but in ONE case (when running sparks
    # tasks on single hosts with -H), Fabric will set it to None
    # and will reset all other attributes (thus we can't use
    # env.host_string.sparks_role for example) and we still
    # need our tasks to figure out the machine's role.
    env.sparks_current_role = None

    if env.host_string:
        if non_empty:

            should_run = False

            for role in non_empty:
                if env.host_string in env.roledefs[role]:
                    should_run = True

                    if not hasattr(env.host_string, 'role') \
                            or env.host_string.role is None:
                        # Supposing we are running via -H, populate the role
                        # manually in a dedicated attribute. Fabric's execute
                        # will reset env.host_string, and it's legitimate if
                        # only -H is given on CLI.
                        env.sparks_current_role = role

                    # No need to look further.
                    break

            if should_run:
                # If the user manually specified a host string / list,
                # we must not add superfluous roles / machines.

                # LOGGER.info(u'Multi-run mode: execute(%s, *%s, **%s) '
                #             u'with %s, %s and %s.', task, args, kwargs,
                #             env.host_string, env.hosts, env.roles)

                # NOTE: don't use Fabric's execute(), it duplicates tasks.
                return task(*args, **kwargs)

            else:
                LOGGER.debug('Not executing %s(%s, %s): host %s not '
                             'in current role(s) “%s”.',
                             getattr(task, 'name', str(task)), args, kwargs,
                             env.host_string, ', '.join(roles))
        else:
            LOGGER.debug('Not executing %s(%s, %s): no role(s) “%s” in '
                         'current context.', getattr(task, 'name', str(task)),
                         args, kwargs, ', '.join(roles))

    else:
        if non_empty:
            kwargs['roles'] = non_empty

            #LOGGER.info('One-shot mode: execute(%s, *%s, **%s)',
            #            task, args, kwargs)

            return execute(task, *args, **kwargs)

        else:
            LOGGER.debug('Not executing %s(%s, %s): no role(s) “%s” in '
                         'current context.', getattr(task, 'name', str(task)),
                         args, kwargs, ', '.join(roles))


def merge_roles_hosts():
    """ Get an exhaustive list of all machines listed
        in the current ``env.roledefs``. """

    merged = []

    for role in env.roledefs:
        merged.extend(env.roledefs[role])

    return merged


def set_roledefs_and_parallel(roledefs, parallel=False):
    """ Define a sparks-compatible but Fabric-happy ``env.roledefs``.
        It's just a shortcut to avoid doing the repetitive:

            env.roledefs = { … }
            # fill env.roledefs with empty lists for each unused roles.
            env.parallel = True
            env.pool_size = …

        Sparks has a default set of roles, already suited for clouded
        web projects. You do not need all of them in all your projects.
        Via this function, you can define only the one you need, and
        sparks will take care of making your ``roledefs`` compatible with
        Fabric, which wants all roles to be defined explicitely.

        Feel free to set :param:`parallel` to True, or any integer >= 1
        to enable the parallel mode. If set to ``True``, the function will
        count merged hosts and set parallel to this number. It defaults
        to ``False`` (no parallel execution).

        .. note:: the pool size is always clamped to 10 hosts to avoid making
            your machine and network suffer. If you ever would like to raise
            this maximum value, just set your shell environment
            variable ``SPARKS_PARALLEL_MAX`` to any integer value you want,
            and don't ever rant.

        .. versionadded:: new in version 2.0.

        .. versionchanged:: in version 2.1, this method was named
            after ``set_roledefs_and_roles_or_hosts``, but the whole process
            was still under design.
    """

    maximum = int(os.environ.get('SPARKS_PARALLEL_MAX', 10))

    if maximum < 2:
        maximum = 10

    env.roledefs = roledefs

    # pre-set empty roles with empty lists to avoid the beautiful:
    #   Fatal error: The following specified roles do not exist:
    #       worker
    for key in all_roles:
        env.roledefs.setdefault(key, [])

    # merge all hosts for tasks that can run on any of them.
    env.roledefs['__any__'] = merge_roles_hosts()

    if parallel is True:
        env.parallel = True
        nbhosts = len(set(env.hosts))
        env.pool_size = maximum if nbhosts > maximum else nbhosts

    else:
        try:
            parallel = int(parallel)

        except:
            pass

        else:
            if parallel > 1:
                env.parallel = True
                env.pool_size = maximum if parallel > maximum else parallel


def generate_random_name():
    return ''.join(
        random.choice(
            string.ascii_uppercase
            + string.digits
            + string.ascii_lowercase
        ) for x in range(10))


def get_current_role():
    """ Return the current role of the current host. Can be ``None`` if
        there is no role in current context. """

    return getattr(env.host_string, 'role', None) or env.sparks_current_role


def worker_information_from_role(role):
    """ Return a tuple of two strings ``(worker_name, worker_queues)``, given
        the parameter :param:`role`. The tuple is simply taken from a
        corresponding table that you can customize
        in ``env.sparks_options['worker_information']``. If the role is not
        a worker one, 2 empty strings will be returned, eg. ``('', '')``.

        .. note:: be respectful when customizing, the current way of doing
            things is kind of weak. It works but it's not rock solid.

        .. versionadded:: 3.4
    """

    if role in worker_roles:
        sparks_information = worker_information.copy()
        custom_information = env.sparks_options.get('worker_information', {})

        sparks_information.update(custom_information)

        info = sparks_information.get(role, None)

        if info is None:
            return role.title(), re.sub('[^\w_]+', '',
                                        role.lower().replace('-', '_'))

        return info

    return '', ''


# ================================================ general-purpose Fabric tasks

try:
    @task
    def get_dir_with_sudo(remote_path, local_path=None):

        with cd(remote_path):
            source_basename = os.path.basename(remote_path)

            print('Downloading {0}:{1}…'.format(env.host_string, remote_path),
                  end='')
            sys.stdout.flush()

            save_file_name = '../{0}-copy-{1}.tar.gz'.format(
                source_basename, generate_random_name())
            while exists(save_file_name):
                save_file_name = '../{0}-copy-{1}.tar.gz'.format(
                    source_basename, generate_random_name())

            # chmod will allow get() to succeed, provided the
            # user has correct access to full containing path…
            sudo("tar -czf '{0}' . ; chmod 644 '{0}'".format(save_file_name),
                 quiet=True)

            with hide('running', 'stdout', 'stderr'):
                downloaded = get(save_file_name, local_path)[0]

            sudo('rm -f "{0}"'.format(save_file_name), quiet=True)

            local_dirname, local_basename = downloaded.rsplit(os.sep, 1)

            local_full_name = os.path.join(local_dirname, source_basename)

            with lcd(local_dirname):
                with hide('running', 'stdout', 'stderr'):
                    local('mkdir -p "{0}"'.format(source_basename))

                    with lcd(source_basename):
                        local('tar -xzf "../{0}"'.format(local_basename))

                    local('rm -f "{0}"'.format(local_basename))

                print('into {1} directory.'.format(remote_path,
                      local_full_name))

                return local_full_name

    @task
    def put_dir_with_sudo(local_path, remote_path):
        # TODO: implement remote_path=None & return remote_path

        with lcd(local_path):
            source_basename = os.path.basename(local_path)

            print('Uploading {0} to {1}:{2}…'.format(local_path,
                  env.host_string, remote_path), end='')
            sys.stdout.flush()

            save_file_name = '../{0}-copy-{1}.tar.gz'.format(
                source_basename, generate_random_name())
            while os.path.exists(save_file_name):
                save_file_name = '../{0}-copy-{1}.tar.gz'.format(
                    source_basename, generate_random_name())

            with hide('running', 'stdout', 'stderr'):
                local("tar -czf '{0}' . ".format(save_file_name))

            remote_dirname, remote_basename = remote_path.rsplit(os.sep, 1)

            with hide('running', 'stdout', 'stderr'):
                put(save_file_name, remote_dirname, use_sudo=True)
                local('rm -f "{0}"'.format(save_file_name))

            with cd(remote_dirname):
                with hide('running', 'stdout', 'stderr'):
                    sudo('mkdir -p "{0}"'.format(remote_basename))

                    with cd(remote_basename):
                        sudo('tar -xzf "{0}"'.format(save_file_name))
                        sudo('rm -f "{0}"'.format(save_file_name))

                print(' done.')

except NameError:
    # Fabric is not yet installed. Don't crash. Happens during the first setup.
    pass


# =================================================== Remote system information

def is_localhost(hostname):
    return hostname in ('localhost', 'localhost.localdomain',
                        '127.0.0.1', '127.0.1.1', '::1')


def is_local_environment():

    is_local = env.environment == 'local' or (
        env.environment == 'test'
            and is_localhost(env.host_string))

    return is_local


def is_development_environment():

    is_development = (is_local_environment()
                      or env.environment in ('test', 'development', 'preview'))

    return is_development


def is_production_environment():

    is_production = (not is_development_environment()
                     and env.environment in ('production', 'real'))

    return is_production


class ConfigurationMixin(object):
    """ Common methods to Remote & Local configuration classes. """

    def __getattr__(self, key):
        """ This lazy getter will allow to load the Django settings after
            Fabric and the project fabfile has initialized `env`. Doing
            elseway leads to cycle dependancy KeyErrors. """

        if key == 'django_settings':
            try:
                self.get_django_settings()

            except ImportError:
                raise AttributeError(u'%s Django settings could not be '
                                     u'loaded.' % ('Remote' if 'Remote'
                                                   in self.__class__.__name__
                                                   else 'Local'))

            return self.django_settings

    @property
    def is_osx(self):
        return self.mac is not None

    @property
    def is_arch(self):
        return self.lsb and self.lsb.ID == 'arch'

    @property
    def is_bsd(self):
        return self.bsd is not None

    @property
    def is_freebsd(self):
        return self.bsd and self.bsd.ID == 'FreeBSD'

    @property
    def is_ubuntu(self):
        return self.lsb and self.lsb.ID.lower() == 'ubuntu'

    @property
    def is_debian(self):
        return self.lsb and self.lsb.ID.lower() == 'debian'

    @property
    def is_deb(self):
        return self.lsb and self.lsb.ID.lower() in ('ubuntu', 'debian',)

    @property
    def release_formatted(self):

        if self.mac:
            return u'Apple OSX {0}'.format(self.mac.release)

        if self.lsb:
            return u'{0} {1}'.format(self.lsb.ID.title(), self.lsb.RELEASE)

        if self.bsd:
            return u'{0} {1}'.format(self.bsd.ID, self.bsd.RELEASE)

    @property
    def vm_formatted(self):
        return ('VMWare '
                if self.is_vmware
                else 'Parallels '
                ) if self.is_vm else ''


class RemoteConfiguration(ConfigurationMixin):
    """ Define an easy to use object with remote machine configuration. """

    def __init__(self, host_string):

        self.host_string = host_string

        # No need to `deactivate` for this calls, it's pure shell.
        self.user, self.tilde = run('echo "${USER},${HOME}"',
                                    quiet=not DEBUG).strip().split(',')

        self.get_platform()
        self.get_uname()
        self.get_virtual_machine()

        if not QUIET:
            print('Remote is {release} {host} {vm}{arch}, user '
                  '{user} in {home}.'.format(
                  release=self.release_formatted,
                  host=cyan(self.uname.nodename),
                  vm=self.vm_formatted,
                  arch=self.uname.machine,
                  user=cyan(self.user),
                  home=self.tilde,
                  ))

    def reload(self):
        """ This methods just reloads the remote Django settings, because
            anything else is very unlikely to have changed. """

        self.get_django_settings()

    def get_platform(self):
        # Be sure we don't get stuck in a virtualenv for free.
        with prefix('deactivate >/dev/null 2>&1 || true'):
            out = run("python -c 'from __future__ import print_function; "
                      "import platform; "
                      "print(platform.system())'",
                      quiet=not DEBUG, combine_stderr=False)

        self.lsb = None
        self.mac = None
        self.bsd = None

        out = out.strip().lower()

        if out == u'linux':
            distro = run("python -c 'from __future__ import print_function; "
                         "import platform; "
                         "print(\",\".join(platform.linux_distribution()))'",
                         quiet=not DEBUG,
                         combine_stderr=False).strip().split(',')

            if distro[0].lower() in ('debian', 'ubuntu'):

                self.lsb          = SimpleObject()
                self.lsb.ID       = distro[0]
                self.lsb.RELEASE  = distro[1]
                self.lsb.CODENAME = distro[2]

            elif distro[0].lower() == 'arch' or (
                distro == ('', '', '')
                    and 'ARCH' in platform.platform()):
                # http://bugs.python.org/issue12214
                # is implemented only for Python 3.3+.
                self.lsb          = SimpleObject()
                self.lsb.ID       = 'arch'
                self.lsb.RELEASE  = platform.release()
                self.lsb.CODENAME = 'ArchLinux'

            else:
                raise RuntimeError(u'Unsupported Linux distro {1} on {0}, '
                                   u'please get in touch with 1flow/sparks '
                                   u'developers.'.format(
                                   self.host_string, distro[0]))

        elif out == u'darwin':

            # Be sure we don't get stuck in a virtualenv for free.
            with prefix('deactivate >/dev/null 2>&1 || true'):
                out = run("python -c 'from __future__ import print_function; "
                          "import platform; "
                          "print(platform.mac_ver())'", quiet=not DEBUG,
                          combine_stderr=False)

            try:
                for line in out.splitlines():
                    if line.startswith("('"):
                        self.mac = SimpleObject(from_dict=dict(zip(
                                                ('release', 'version',
                                                 'machine'),
                                                ast.literal_eval(line))))
                        break

            except SyntaxError:
                # something went very wrong,
                # none of the detection methods worked.
                raise RuntimeError(u'Cannot determine platform of {0}, '
                                   u'platform.mac_ver() reported nothing '
                                   u'usable:\n{1}'.format(self.host_string,
                                   out))
            else:
                if self.mac is None:
                    raise RuntimeError(u'Cannot determine platform of {0}, '
                                       u'platform.mac_ver() reported nothing '
                                       u'usable:\n{1}'.format(self.host_string,
                                       out))

        elif out == u'freebsd':
            release = run("python -c 'from __future__ import print_function; "
                         "import platform; "
                         "print(platform.release())'",
                         quiet=not DEBUG,
                         combine_stderr=False).strip()

            self.bsd = SimpleObject()
            self.bsd.ID = 'FreeBSD'
            self.bsd.RELEASE = release
            self.bsd.VERSION = release.split('-')[0]
            self.bsd.MAJOR   = int(self.bsd.VERSION.split('.')[0])
            self.bsd.MINOR   = int(self.bsd.VERSION.split('.')[1])

        else:
            raise RuntimeError(u'Unsupported platform {1} on {0}, please '
                               u'get in touch with 1flow/sparks '
                               u'developers.'.format(self.host_string, out))

    def get_uname(self):
        # Be sure we don't get stuck in a virtualenv for free.
        with prefix('deactivate >/dev/null 2>&1 || true'):
            out = run("python -c 'from __future__ import print_function; "
                      "import os; print(os.uname())'",
                      quiet=not DEBUG, combine_stderr=False)

        self.uname = None

        for line in out.splitlines():
            if line.startswith("('"):
                self.uname = SimpleObject(from_dict=dict(zip(
                                          ('sysname', 'nodename', 'release',
                                          'version', 'machine'),
                                          ast.literal_eval(line))))
                break

            # Python 3.x (tested on ArchLinux 20140826)
            if line.startswith("posix.uname_result("):
                self.uname = SimpleObject(from_dict=ast.literal_eval(
                                          'dict' + line[18:]))
                break

        if self.uname is None:
            raise RuntimeError(u'cannot determine uname of {0}, '
                               u'os.uname() reported nothing usable:\n'
                               u'{1}'.format(self.host_string, out))

        self.hostname = self.uname.nodename

    def get_virtual_machine(self):
        # TODO: implement me (and under OSX too).
        self.is_vmware = False

        # NOTE: this test could fail in VMs where nothing is mounted from
        # the host. In my own configs, this never occurs, but who knows.
        # TODO: check this works under OSX too, or enhance the test.
        self.is_parallel = run('mount | grep prl_fs', quiet=not DEBUG,
                               warn_only=True, combine_stderr=False).succeeded

        self.is_vm = self.is_parallel or self.is_vmware

    def get_django_settings(self):

        # transform the supervisor syntax to shell syntax.
        env_generic = ' '.join(env.environment_vars) \
            if hasattr(env, 'environment_vars') else ''

        env_sparks = (' SPARKS_DJANGO_SETTINGS={0}'.format(
                      env.sparks_djsettings)) \
            if hasattr(env, 'sparks_djsettings') else ''

        env_django_settings = \
            ' DJANGO_SETTINGS_MODULE="{0}.settings"'.format(env.project)

        # Here, we *NEED* to be in the virtualenv, to get the django code.
        # NOTE: this code is kind of weak, it will fail if settings include
        # complex objects, but we hope it's not.

        prefix_cmd = 'workon {0}'.format(env.virtualenv) \
            if hasattr(env, 'virtualenv') else ''

        pickled_settings = StringIO.StringIO()

        with prefix(prefix_cmd):
            with cd(env.root if hasattr(env, 'root') else ''):
                # NOTE: this doesn't work with “ with open(…) as f: ”, even
                # though I would have greatly prefered this modern version…
                out = run(("{0}{1}{2} python -c 'import cPickle as pickle; "
                          "from django.conf import settings; "
                          "settings._setup(); "
                          "f=open(\"__django_settings__.pickle\", "
                          "\"w\"); pickle.dump(settings._wrapped, f, "
                          "pickle.HIGHEST_PROTOCOL); f.close()'").format(
                          env_generic, env_sparks, env_django_settings),
                          quiet=not DEBUG, warn_only=True, combine_stderr=False)

                if out.succeeded:
                    get('__django_settings__.pickle',
                        pickled_settings)
                    run('rm -f __django_settings__.pickle',
                        quiet=not DEBUG)

                    try:
                        self.django_settings = pickle.loads(
                            pickled_settings.getvalue())

                    except:
                        LOGGER.exception('Cannot load remote django settings!')

                    pickled_settings.close()

                else:
                    LOGGER.warning(('Could not load remote Django settings '
                                   'for project "{0}" (which should be '
                                   'located in "{1}", with env. {2}{3}'
                                   ')').format(
                                   env.project,
                                   env.root if hasattr(env, 'root') else '~',
                                   env_generic, env_sparks))
                    raise ImportError


class LocalConfiguration(ConfigurationMixin):
    """ Define an easy to use object with local machine configuration.

        This class doesn't use fabric, it's used to bootstrap the local
        machine when it's empty and doesn't have fabric installed yet.

        .. warning:: this class won't probably play well in a virtualenv.
            Unlike the :class:`RemoteConfiguration` class, I don't think
            it's pertinent and wanted to :program:`deactivate` first.
     """
    def __init__(self, host_string=None):

        self.host_string = host_string or 'localhost'

        system = platform.system().lower()

        self.lsb = None
        self.mac = None
        self.bsd = None

        if system == 'linux':
            distro = platform.linux_distribution()

            if distro[0].lower() in ('debian', 'ubuntu'):
                self.lsb          = SimpleObject()
                self.lsb.ID       = distro[0]
                self.lsb.RELEASE  = distro[1]
                self.lsb.CODENAME = distro[2]

            elif distro[0].lower() == 'arch' or (
                    distro == ('', '', '') and 'ARCH' in platform.platform()):
                # http://bugs.python.org/issue12214
                # is implemented only for Python 3.3+.
                self.lsb          = SimpleObject()
                self.lsb.ID       = 'arch'
                self.lsb.RELEASE  = platform.release()
                self.lsb.CODENAME = 'ArchLinux'

            else:
                raise RuntimeError(u'Unsupported Linux distro {1} on '
                                   u'localhost, please get in touch with '
                                   u'1flow/sparks developers.'.format(
                                       distro[0]))

        elif system == 'darwin':
            self.mac    = SimpleObject(from_dict=dict(zip(
                                       ('release', 'version', 'machine'),
                                       platform.mac_ver())))

        elif system == u'freebsd':
            release = platform.release()

            self.bsd = SimpleObject()
            self.bsd.ID = 'FreeBSD'
            self.bsd.RELEASE = release
            self.bsd.VERSION = release.split('-')[0]
            self.bsd.MAJOR   = int(self.bsd.VERSION.split('.')[0])
            self.bsd.MINOR   = int(self.bsd.VERSION.split('.')[1])

        else:
            raise RuntimeError(u'Unsupported platform {0} on localhost, '
                               u'please get in touch with 1flow/sparks '
                               u'developers.'.format(system))

        self.uname = SimpleObject(from_dict=dict(zip(
                                  ('sysname', 'nodename', 'release',
                                  'version', 'machine'),
                                  os.uname())))

        self.hostname = self.uname.nodename

        self.user, self.tilde = nofabric.local('echo "${USER},${HOME}"',
                                               ).strip().split(',')

        # TODO: implement me (and under OSX too).
        self.is_vmware = False

        # NOTE: this test could fail in VMs where nothing is mounted from
        # the host. In my own configs, this never occurs, but who knows.
        # TODO: check this works under OSX too, or enhance the test.
        self.is_parallel = nofabric.local('mount | grep prl_fs').succeeded

        self.is_vm = self.is_parallel or self.is_vmware

    def __getattr__(self, key):
        """ This lazy getter will allow to load the Django settings after
            Fabric and the project fabfile has initialized `env`. Doing
            elseway leads to cycle dependancy KeyErrors. """

        if key == 'django_settings':
            try:
                self.get_django_settings()

            except ImportError:
                raise AttributeError(
                    'Local Django settings could not be loaded.')

            return self.django_settings

    def get_django_settings(self):

        # Set the environment exactly how it should be for runserver.
        # Supervisor environment can hold the sparks settings,
        # while Django environment will hold the project settings.

        if hasattr(env, 'environment_vars'):
            for env_var in env.environment_vars:
                name, value = env_var.strip().split('=')
                os.environ[name] = value

        if hasattr(env, 'sparks_djsettings'):
            os.environ['SPARKS_DJANGO_SETTINGS'] = env.sparks_djsettings

        os.environ['DJANGO_SETTINGS_MODULE'] = \
            '{0}.settings'.format(env.project)

        # Insert the $CWD in sys path, and pray for the user to have called
        # `fab` from where `manage.py` is. This is the way it should be done
        # but who knows…
        current_root = env.root if (hasattr(env, 'root')
                                    and is_local_environment()) else os.getcwd()
        sys.path.append(current_root)

        try:
            from django.conf import settings as django_settings
            # Avoid Django to (re-)configure our own logging;
            # the Fabric output becomes a mess without this.
            django_settings.__class__._configure_logging = lambda x: None

            django_settings._setup()

        except ImportError:
            LOGGER.warning(('Django settings could not be loaded for '
                           'project "{0}" (which should be '
                           'located in "{1}", with env. {2}{3}'
                           ')').format(
                           env.project,
                           current_root,
                           'SPARKS_DJANGO_SETTINGS={0}'.format(
                           env.sparks_djsettings)
                           if hasattr(env, 'sparks_djsettings') else '',
                           ' '.join(env.environment_vars)
                           if hasattr(env, 'environment_vars') else ''))
            raise
        else:
            self.django_settings = django_settings._wrapped

        finally:
            sys.path.remove(current_root)

            del os.environ['DJANGO_SETTINGS_MODULE']

            if hasattr(env, 'sparks_djsettings'):
                del os.environ['SPARKS_DJANGO_SETTINGS']

            if hasattr(env, 'environment_vars'):
                for env_var in env.environment_vars:
                    del os.environ[name]


def with_remote_configuration(func):

    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        global remote_configuration
        if remote_configuration is None:
            try:
                remote_configuration = find_configuration_type(env.host_string)
            except NameError:
                # no 'env', probably running from 1nstall.
                remote_configuration = find_configuration_type('localhost')

        elif remote_configuration.host_string != env.host_string:
            # host changed: fabric is running the same task on another host.
            remote_configuration = find_configuration_type(env.host_string)

        # Insert remote_configuration directly in kwargs.
        # This avoids the following error:
        #    TypeError: XXX() got multiple values
        #    for keyword argument 'remote_configuration'
        # at the price of some overwriting. We just hope that no-one
        # will have the bad idea of naming his KWargs the same.
        kwargs['remote_configuration'] = remote_configuration

        return func(*args, **kwargs)

    return wrapped


def with_local_configuration(func):

    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        global local_configuration
        if local_configuration is None:
            local_configuration = LocalConfiguration()

        return func(*args, local_configuration=local_configuration, **kwargs)

    return wrapped


def find_configuration_type(hostname):

    if is_localhost(hostname):
        return LocalConfiguration()

    else:
        return RemoteConfiguration(hostname)


if QUIET:
    logging.basicConfig(format=
                        '%(asctime)s %(name)s[%(levelname)s] %(message)s',
                        level=logging.WARNING)

    if not os.environ.get('SPARKS_PARAMIKO_VERBOSE', False):
        # but please, no paramiko, it's just flooding my terminal.
        logging.getLogger('paramiko').setLevel(logging.ERROR)

else:
    logging.basicConfig(format=
                        '%(asctime)s %(name)s[%(levelname)s] %(message)s',
                        level=logging.INFO)

    if not os.environ.get('SPARKS_PARAMIKO_VERBOSE', False):
        # but please, no paramiko, it's just flooding my terminal.
        logging.getLogger('paramiko').setLevel(logging.WARNING)

if local_configuration is None:
    local_configuration = LocalConfiguration()
