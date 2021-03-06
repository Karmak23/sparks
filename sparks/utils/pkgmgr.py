#!/usr/bin/env python
# -*- coding: utf8 -*-

import os
import sys
import multiprocessing

# Use this in case paramiko seems to go crazy. Trust me, it can do, especially
# when using the multiprocessing module.
#
#import logging
#logging.basicConfig(format=
#                    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#                    level=logging.INFO)

from fabric.api import env
from fabric.colors import yellow, red, green, cyan, blue

if __package__ is None:
    # See ../fabfile.py for explanations
    sys.path.append(os.path.expanduser('~/Dropbox'))

from sparks import pkg

env.host_string = 'localhost'
colors = [yellow, green, cyan, blue]
colnum = len(colors)


#LOGGER = logging.getLogger(__name__)


def usable(module, suffix, name):
    l = len(suffix)
    usable_name = name[:-l] + '_usable'

    #print '>>', usable_name, getattr(module, usable_name)() \
    #       if hasattr(module, usable_name) else ''

    try:
        return getattr(module, usable_name)()

    except AttributeError:
        # no usability condition, it's OK to use.
        #print '>>', usable_name, 'FORCE True'
        return True


def lookup(module, suffix):

    # TODO: cache after the first lookup for current execution/suffix.

    index = 0
    for k, v in module.__dict__.iteritems():
        if k.endswith(suffix) and callable(v) and usable(module, suffix, k):
            yield index, k, v
            index += 1


def names(module, suffix):
    l = len(suffix)
    return ', '.join(colors[i % colnum](k[:-l]) for i, k, v
                     in lookup(module, suffix))


def encapsulate(func, name, args, suffix, index):

    # fancy pkg-manager name
    name = colors[index % colnum](name[:-len(suffix)].upper())

    for result in func(args):
        for line in result.splitlines():
            print('%s %s' % (name, line))


def wrap(module, suffix, args):

    ps = []

    for index, name, func in lookup(module, suffix):
        p = multiprocessing.Process(target=encapsulate,
                                    args=(func, name, args, suffix, index, ))
        ps.append(p)
        p.start()

    for p in ps:
        p.join()


def search(args):
    sys.stderr.write(u'>> searching {0} in {1}…\n'.format(
                     u', '.join(red(a) for a in args),
                     names(pkg, '_search')))

    wrap(pkg, '_search', args)


def install(args):
    pass


def remove(args):

    pass


def purge(args):
    pass


def main():

    index = 1 if 'python' in sys.argv[0] else 0

    pgm_name = os.path.basename(sys.argv[index])
    args = sys.argv[index + 1:]

    try:
        globals()[pgm_name](args)

    except KeyError:
        sys.exit(1)


if __name__ == '__main__':
    main()
