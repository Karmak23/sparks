# -*- coding: utf-8 -*-
"""
    Django sparks.

"""

import sys


def create_admin_user(email=None, password=None):

    # additional process for creating an admin without input or misc data…
    # cf. http://stackoverflow.com/a/13466241/654755
    for arg in sys.argv:
        if arg.lower() == 'syncdb':
            print 'syncdb post process…'
            from django.contrib.auth.models import User
            from django.conf import settings

            admin_id       = 'admin'
            admin_email    = email or 'contact@oliviercortes.com'
            admin_password = password or ('admin'
                                          if settings.DEBUG
                                          else 'SET_ME')

            try:
                User.objects.get(username=admin_id)

            except:
                User.objects.create_superuser(admin_id,
                                              admin_email,
                                              admin_password)
