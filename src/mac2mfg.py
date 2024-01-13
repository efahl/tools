#!/usr/bin/env python3
"""
Copyright (C) 2022-2024 Eric Fahlgren
SPDX-License-Identifier: GPL-2.0

Look up the manufacturer of MAC addresses.  Does not care about character
case, and allows either dashes or colons as separators.

>>> ./mac2mfg.py 18:fe:34:fc:02:c3 00-15-5D-01-A0-0E
>>> 18fe34 Espressif Inc.
>>> 00155d Microsoft Corporation

The API version returns a tuple containing both the prefix that was found
in the table, and the manufacturer's name:

>>> from mac2mfg import mac2mfg
>>> print(mac2mfg('18:fe:34:fc:02:c3'))
>>> ('18fe34', 'Espressif Inc.')
>>> print(mac2mfg('1f:ff:34:fc:02:c3'))
>>> ('1fff34', '[unknown mfg]')

If you have too many misses, use the WireShark 'make-manuf.py' in .local/bin
to update the 'manuf' file.  If the entries remain undefined, you can add
them to the 'manuf.tmpl' file and regenerate.  Note that some devices (Android
and newer iOS phones) generate random MAC addresses to avoid fingerprinting,
so they will always appear to as devices from an unknown manufacturer.

2023-09-08 - Use the 'make-manuf2.py' script to update 'manuf_oui24.py', as
we no longer read and parse the 'manuf' file, but rather create a python
version of the data.

"""
#-------------------------------------------------------------------------------


import os


install_dir = os.path.dirname(os.path.realpath(__file__))  # Chase through any symbolic link from cgi-bin.

def read_manuf_file():
    """ Build a dict of mac:mfgr pairs from the WireShark 'manuf' file. """
    mac_table = {}

#   def add_sep(S, sep, at=2):
#       """ Insert 'sep' into 'S' spaced 'at'. """"
#       return sep.join(S[i:i+2] for i in range(0, len(S), at))
#
#   with open('/usr/share/nmap/nmap-mac-prefixes') as mac_prefix:
#       for line in mac_prefix:
#           line = line.split('#')[0].strip()
#           if line:
#               mac, mfg = line.split(' ', 1)
#               mac = add_sep(mac, ':', 2)

    with open(os.path.join(install_dir, 'manuf')) as mac_prefix:
        for line in mac_prefix:
            line = line.split('#')[0].strip()
            if line:
                # Subsequent use doesn't handle the /28 or /36 entries in table.
                info = line.split('\t')
                mac, mfg = info[0], info[-1]
                mac_table[mac.lower().replace(':', '')] = mfg
    return mac_table

from manuf_oui24 import manuf_oui24_table

def mac2mfg(mac):
    """ See RFC 7844 for MAC Address Randomization. """
    prefix = mac.lower().replace('-', '').replace(':', '')[:6]
    default = '[randomized MAC]' if prefix[1] in '26ae' else '[unknown mfg]'
    return prefix, manuf_oui24_table.get(prefix, ['', default])[1]

if __name__ == '__main__':
    import sys
    for mac in sys.argv[1:]:
        print(*mac2mfg(mac))
