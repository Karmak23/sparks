# -*- coding: utf-8 -*-
"""
    PostgreSQL sparks helpers.

    For the Django developers to be able to create users & databases in
    complex architectures (eg. when the DB server is not on the Django
    instance server…) you must first define a PostgreSQL restricted admin
    user to manage the Django projects and apps databases.

    This user doens't need to be strictly ``SUPERUSER``, though.
    Having ``CREATEDB`` and ``CREATEUSER`` will suffice (but ``CREATEROLE``
    won't). For memories, this is how I created mine, on the central
    PostgreSQL server::

        # OPTIONAL: I first give me some good privileges
        # to avoid using the `postgres` system user.
        sudo su - postgres
        createuser --login --no-inherit \
            --createdb --createrole --superuser MYUSERNAME
        psql
            ALTER USER MYUSERNAME WITH ENCRYPTED PASSWORD 'MAKE_ME_STRONG';
        [exit]

        # Then, I create the other admin user which will handle all fabric
        # requests via developer tasks.
        psql
            CREATE ROLE oneflow_admin PASSWORD '<passwd>' \
                CREATEDB CREATEUSER NOINHERIT LOGIN;

        # NOTE: NOSUPERUSER conflicts with CREATEUSER.

        # Already done in previous command,
        # but keeing it here for memories.
        #    ALTER USER oneflow_admin WITH ENCRYPTED PASSWORD '<passwd>';

"""

import os
import pwd
import logging
from fabric.api import env, sudo
from ..fabric import with_remote_configuration, is_local_environment

LOGGER = logging.getLogger(__name__)

# Never ask for a password, return tuples only (no headers), execute command.
BASE_CMD = '{pg_env} psql -wtc "{sqlcmd}"'

# {pg_env} is intentionnaly repeated, it will be filled later.
# Without repeating it, `.format()` will fail with `KeyError`.
SELECT_USER = BASE_CMD.format(pg_env='{pg_env}',
                              sqlcmd="SELECT usename from pg_user "
                              "WHERE usename = '{user}';")
CREATE_USER = BASE_CMD.format(pg_env='{pg_env}',
                              sqlcmd="CREATE USER {user} "
                              "WITH PASSWORD '{password}';")
ALTER_USER  = BASE_CMD.format(pg_env='{pg_env}',
                              sqlcmd="ALTER USER {user} "
                              "WITH ENCRYPTED PASSWORD '{password}';")
SELECT_DB   = BASE_CMD.format(pg_env='{pg_env}',
                              sqlcmd="SELECT datname FROM pg_database "
                              "WHERE datname = '{db}';")
CREATE_DB   = BASE_CMD.format(pg_env='{pg_env}',
                              sqlcmd="CREATE DATABASE {db} OWNER {user};")


def wrapped_sudo(*args, **kwargs):
    """ Avoid the nasty "Sessions still open" false-positive error on ecryptfs.
        Eg.:

        [localhost] sudo: PGHOST=127.0.0.1 psql -tc "ALTER USER ···"
        [localhost] out:
        [localhost] out: Sessions still open, not unmounting
        [localhost] out:

        Fatal error: sudo() received nonzero return code 1 while executing!

        Thanks http://serverfault.com/q/415602/166356

    """

    if 'warn_only' in kwargs:
        #original_must_fail = False
        pass

    else:
        #original_must_fail  = True
        kwargs['warn_only'] = True

    result = sudo(*args, **kwargs)

    if u'Sessions still open, not unmounting' in result:

        from ..fabric.nofabric import _AttributeString

        result2 = _AttributeString(result.replace(
                                   u'Sessions still open, '
                                   u'not unmounting', u''))

        #if original_must_fail:
        #    # Until Fabric 2.0, we have no exception class
        #    # cf. https://github.com/fabric/fabric/issues/277
        #    raise Exception('Command failed')

        if result.failed:
            result2.failed    = False
            result2.succeeded = True

        result = result2

    return result


@with_remote_configuration
def get_admin_user(remote_configuration=None):

    environ_user = os.environ.get('SPARKS_PG_SUDO_USER', None)

    if environ_user is not None:
        LOGGER.info('Using environment variable SPARKS_PG_SUDO_USER.')
        return environ_user

    if remote_configuration.lsb:
        # FIXED: on Ubuntu / Debian, it's been `postgres` since ages.
        return 'postgres'

    elif remote_configuration.is_bsd:
        return 'pgsql'

    elif remote_configuration.is_osx:
        # On OSX where PG is installed via brew, the local
        # user is admin, there is no "postgres" admin user.
        return pwd.getpwuid(os.getuid()).pw_name

    else:
        raise NotImplementedError("Don't know how to find PG user "
                                  "on remote server other than LSB.")


@with_remote_configuration
def temper_db_args(remote_configuration=None,
                   db=None, user=None, password=None):
    """ Try to accomodate with DB creation arguments.

        If all of them are ``None``, the function will try to fetch
        them automatically from the remote server Django settings.

    """

    if db is None and user is None and password is None:
        djsettings = getattr(remote_configuration, 'django_settings', None)

        if djsettings is None:
            raise ValueError('No database parameters supplied and no '
                             'remote Django settings available!')

        else:
            # if django settings has 'test' or 'production' (=env.environment)
            # DB, get it. Else get 'default' because all settings have it.
            db_settings = djsettings.DATABASES.get(
                env.environment, djsettings.DATABASES['default'])

            db       = db_settings['NAME']
            user     = db_settings['USER']
            password = db_settings['PASSWORD']

    if db is None:
        if user is None:
            raise ValueError('Parameters db and user '
                             'cannot be `None` together.')

        db = user

    else:
        if user is None:
            user = db

    if password is None:
        if not is_local_environment():
            raise ValueError('Refusing to set the username as password '
                             'in a production environment. Seriously?')

        password = user

    return db, user, password
