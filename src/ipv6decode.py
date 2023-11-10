#!/usr/bin/env python3
# vim: set expandtab softtabstop=4 shiftwidth=4:
# Copyright (C) 2022 Eric Fahlgren
# Released under GPL2.0  https://opensource.org/licenses/gpl-2.0.php
#-------------------------------------------------------------------------------

import os
import json
import ipaddress
from   collections import namedtuple
from   mac2mfg     import mac2mfg
from   whois       import whois_from_cache, WhoIs

install_dir = os.path.dirname(os.path.realpath(__file__))  # Chase through any symbolic link from cgi-bin.

def parse_args():
    from argparse import ArgumentParser

    fmt_opt = 'long', 'short'
    zero    = '\N{GURMUKHI DIGIT ZERO}'

    see_also = 'See also: https://blogs.infoblox.com/ipv6-coe/fe80-1-is-a-perfectly-valid-ipv6-default-gateway-address/'
    parser = ArgumentParser(epilog=see_also)
    parser.add_argument('-v', '--verbose',  default=0,          dest='verbosity', action='count',      help='Increase the verbosity level each time you specify it.')
    parser.add_argument('-f', '--format',   default=fmt_opt[0],                   choices=fmt_opt,     help='Address format options.  Default: %(default)r.')
    parser.add_argument(      '--sort',     default=False,                        action='store_true', help='Sort the input addresses from most-global to most-local.')
    # https://www.compart.com/en/unicode/search?q=zero#characters
    parser.add_argument(      '--zero',     default=zero,                         action='store',      help='Characters to use for 4x zero sequence.  Default: %(default)r (Gurmukhi Digit Zero).')
    parser.add_argument(      '--exploded', default=False,                        action='store_true', help='Map the address blocks to binary bit fields.')
    parser.add_argument(      '--testing',  default=False,                        action='store_true', help='Use canned addresses to show stuff and quit.')
    parser.add_argument(      '--list',     default=False,                        action='store_true', help='List internal allocation table and quit.')
    parser.add_argument(                    default=list(),     dest='addresses', nargs='*',           help='All the IP addresses.')
    args = parser.parse_args()

    if len(args.zero) < 4:
        args.zero = 4*args.zero
    if len(args.zero) > 4:
        args.zero = args.zero[:4]

    if args.testing:
        args.addresses = (
            '::',
            '::1',
            '::ffff:192.168.1.200',
            '2001:0000:4136:e378:8000:63bf:3fff:fdd2',
            '2001:0002::6c:ab:a',
            '2001:0004:112::48',
            '2001:db8:8:4::2:1',
            '2002:624:624::16',
            '2002:cb0a:3cdd:1::1',
            '2600:8802:4200:59:4c59:11ed:5359:49f6/64',
            '2600:8802:4200:f:a5f9:9e68:58d7:1d63/64',
            '2620:4f:8000::112:112:48',
            '2620:fe::9/48',
            '2620:fe::fe/48',
            '2a00:ffff:ffff:ffff:ffff:ffff:ffff:ffff',
            '64:ff9b::192.168.1.200',
            'abcd:1234::', # Unassigned block
            'fd91:f453:ed1f:0:a02a:7d9e:7da:40d2',
            'fe80::4c59:11ed:5359:49f6',
            'fec0:0:0:ffff::1',
            'ff01:0:0:0:0:0:0:2',
            'FF05:0:0:0:0:0:1:3', # Site-Local All DHCP Servers, https://www.iana.org/assignments/ipv6-multicast-addresses/ipv6-multicast-addresses.xhtml

            'fdee:dead:beef::1afe:34ff:fefb:01c3',

            # G Linode
            '2600:3c03::f03c:92ff:fe41:3428/64',
            'fddd:1194:1194:1194::1',
            'fe80::3aad:a999:6c93:5b1a',
            'fe80::f03c:92ff:fe41:3428',

            '2a01:4f8:c0c:9e5b::1/64',     # Random Hetzner DE server.
            '2a03:b0c0:3:d0::1af1:1/128',  # OpenWrt on Digital Ocean.
            '2804:49c:3102:401:ffff:ffff:ffff:36', # Some random Brazilian/LACNIC site.
        )

    if args.sort:
        args.addresses = sorted(args.addresses, key=lambda a: ipaddress.IPv6Interface(a).exploded)

    return args

#-------------------------------------------------------------------------------

Block = namedtuple('Block', ('address_block', 'name', 'rfc', 'allocation_date', 'termination_date', 'source', 'destination', 'forwardable', 'globally_reachable', 'reserved_by_protocol'))

def B(addr, name, *args):
    """ Supply an address or address block (if no CIDR is specified, /128 is
        assumed), along with various information.  The second argument,
        `name`, is most important after the address block.
    """
    if '/' not in addr:
        addr += '/128'
    if len(args) < len(Block._fields)-2:
        defaults = '', '', '', None, None, None, None, None
        args     = args + defaults[len(args):]
    return Block(ipaddress.IPv6Network(addr), name, *args)

def in_allocations(address):
    address = address.lower()
    for b in allocations:
        if address == b.address_block.with_prefixlen:
            return b
    return None

def add_new_allocation(addr, name, *args):
    allocations.append(B(addr, name, *args))
    allocations.sort()


def add_org(address):
    """ Search the RIR's RDAPs for the whois entry on this address and add
        any information regarding containing subnets.
    """
    debug = False
    if debug: print('lookup', address)
    whois = whois_from_cache(address)
    if whois:
        root_cidr = whois.cidr
        if block := in_allocations(root_cidr):
            if debug:
                print('  already there', block)
        else:
            desc = f'{whois.name}: {whois.owner}'
            if whois.asn:
                desc += f' (ASN {whois.asn})'
            add_new_allocation(root_cidr, desc)

        parent = whois.parent
        if debug: print('   parent', parent)
        if parent:
            if not parent.startswith('NET'):
                try:
                    parent = ipaddress.IPv6Interface(parent)
                except ipaddress.AddressValueError:
                    parent = None
                else:
                    parent = parent.with_prefixlen
            if parent:
                add_org(parent)


# Add entries to the 'allocations' list in arbitrary order, whatever makes it
# look nice.  The table is sorted by subnet, so that the order here is of no
# consequence.
#
# We sort to make sure supernets are prior to their subnets, then we can just
# run through the list and make assumptions about order.

allocations = sorted((
    # For a global overview, see:
    # https://www.apnic.net/get-ip/faqs/what-is-an-ip-address/ipv6-address-types/

    # Reserved blocks/addresses
    # From https://www.iana.org/assignments/iana-ipv6-special-registry/iana-ipv6-special-registry.xhtml

    #  Address                                                                               Allocation  Termination                                    Globally   Reserved-by
    #  Block                Name                                         RFC                 Date        Date         Source  Destination  Forwardable  Reachable  Protocol
    B('::',                'Unspecified Unicast Address',               'RFC4291',          '2006-02',   '',          True,   False,       False,       False,     True ),
    B('::1',               'Loopback Unicast',                          'RFC4291',          '2006-02',   '',          False,  False,       False,       False,     True ),
IP4:=B('::ffff:0:0/96',    'IPv4-mapped Unicast',                       'RFC4291',          '2006-02',   '',          False,  False,       False,       False,     True ),
    B('0064:ff9b::/96',    'IPv4-IPv6 Translat.',                       'RFC6052',          '2010-10',   '',          True,   True,        True,        True,      False),
    B('0064:ff9b:1::/48',  'IPv4-IPv6 Translat.',                       'RFC8215',          '2017-06',   '',          True,   True,        True,        False,     False),
    B('0100::/64',         'Discard-Only Address Block',                'RFC6666',          '2012-06',   '',          True,   True,        True,        False,     False),
    B('0200::/7',          'DEPRECATED OSI NSAP-mapped prefix set',     'RFC4048 RFC4548',  '',          '2004'),

# But wait!  There's more.
# https://www.iana.org/assignments/ipv6-address-space/ipv6-address-space.xhtml

ULA:=B('fc00::/7',         'Unique Local Unicast (ULA)',                'RFC4193 RFC8190',  '2005-10',   '',          True,   True,        True,        False,     False),
    B('fc00::/8',          'Invalid ULA (0 at bit-8)'),
    B('fd00::/8',          'Valid ULA (1 at bit-8)'),
    B('fe80::/10',         'Link-Local Unicast (LLA) Subnet ID == 0',   'RFC4291',          '2006-02',   '',          True,   True,        False,       False,     True ),
    B('fec0::/10',         'DEPRECATED Site-Local Unicast (SLA)',       'RFC3513 RFC3879',  '2003-04',   '2004-09',   True,   True,        True,        False,     False),

    # Multicast
    # https://www.iana.org/assignments/ipv6-multicast-addresses/ipv6-multicast-addresses.xhtml
    B('ff00::/8',          'Multicast',                                 'RFC4291',          '2006-02'),
    B('ff00::/12',         'Well-Known Multicast',                      'RFC4291'),
    B('ff10::/12',         'Transient Multicast',                       'RFC4291'),
    B('ff30::/12',         'Transient Prefix-Based Multicast',          'RFC4291'),
    B('ff70::/12',         'Transient Prefix-Based Multicast Rendezvous','RFC4291'),

    B('ff02::1:ff00:0/104','Solicited-Node Multicast',                  'RFC4291#section-2.7.1'),

    # Each of these scopes may applied to any of the /12 multicast ranges, but some combinations are nonsensical.
    # I.e., ffXS:... combinations are defined.  See 4191 section 2.7 and
    # https://www.computernetworkingnotes.com/networking-tutorials/ipv6-multicast-addresses-explained.html
    B('ff01::/16',         'Interface-Local Scope',                     'RFC4291',          '2006-02'),
    B('ff02::/16',         'Link-Local Scope',                          'RFC4291',          '2006-02'),
    B('ff03::/16',         'Realm-Local Scope',                         'RFC4291',          '2006-02'),
    B('ff04::/16',         'Admin-Local Scope',                         'RFC4291',          '2006-02'),
    B('ff05::/16',         'Site-Local Scope',                          'RFC4291',          '2006-02'),
    B('ff08::/16',         'Organization-Local Scope',                  'RFC4291',          '2006-02'),
    B('ff0e::/16',         'Global Scope',                              'RFC4291',          '2006-02'),

    B('ff01::1',           'All Nodes Address',                         'RFC4291'),
    B('ff01::2',           'All Routers Address',                       'RFC4291'),
    B('ff01::fb',          'mDNSv6 (Multicast Domain Name System)',     'RFC6762'),
    B('ff01::101',         'All NTP Servers',                           'RFC4291'),

    B('ff02::1',           'All Nodes Address',                         'RFC4291'),
    B('ff02::2',           'All Routers Address',                       'RFC4291'),
    B('ff02::5',           'OSPFIGP All Routers Address',               'RFC4291'),
    B('ff02::6',           'OSPFIGP Designated Routers Address',        'RFC4291'),
    B('ff02::9',           'RIP2 Routers Address',                      'RFC4291'),
    B('ff02::a',           'EIGRP Routers Address',                     'RFC4291'),
    B('ff02::1:2',         'All DHCPv6 servers and relay agents'),
    B('ff02::c',           'SSDP (Simple Service Discovery Protocol)'),
    B('ff02::16',          'ICMPv6 (Multicast Listener Report)'),
    B('ff02::fb',          'mDNSv6 (Multicast Domain Name System)',     'RFC6762'),

    B('ff05::2',           'All Routers Address',                       'RFC4291'),
    B('ff05::fb',          'mDNSv6',                                    'RFC6762'),
    B('ff05::1:3',         'All DHCP Servers',                          'RFC8415'),
    B('ff05::1:4',         'DEPRECATED (2003-03-12)'),
    B('ff05::1:5',         'SL-MANET-ROUTERS',                          'RFC6621'),

    #  Address                                                                               Allocation  Termination                                    Globally   Reserved-by
    #  Block                Name                                         RFC                 Date        Date         Source  Destination  Forwardable  Reachable  Protocol
GUA:=B('2000::/3',         'Global Unicast (GUA)'),

    # Authority Assignments (sort of, the IANA ones are reserved)
    # https://www.iana.org/assignments/ipv6-unicast-address-assignments/ipv6-unicast-address-assignments.xhtml
    # Note the two /12 -> /11 for ARIN and RIPE, NRO reports show the change occurred in 2019:
    # https://www.nro.net/about/rirs/statistics/
    #
    # https://www.nro.net/wp-content/uploads/NRO-Statistics-2019-Q1.pdf  Both /12 2019-03-31
    # https://www.nro.net/wp-content/uploads/NRO-Statistics-2019Q2.pdf   Ripe /11 2019-06-30
    # Q3 - no change
    # https://www.nro.net/wp-content/uploads/NRO-Statistics-2019-Q4.pdf  Both /11 2019-12-31

#   B('2001:0200::/23',    'APNIC',                                     '',                  '1999-07-01'),
#   B('2001:0400::/23',    'ARIN',                                      '',                  '1999-07-01'),
#   B('2001:0600::/23',    'RIPE NCC',                                  '',                  '1999-07-01'),
#   B('2001:0800::/22',    'RIPE NCC',                                  '',                  '2002-11-02'),
#   B('2001:0c00::/23',    'APNIC',                                     '',                  '2002-05-02'),
#   B('2001:0e00::/23',    'APNIC',                                     '',                  '2003-01-01'),
#   B('2001:1200::/23',    'LACNIC',                                    '',                  '2002-11-01'),
#   B('2001:1400::/22',    'RIPE NCC',                                  '',                  '2003-07-01'),
#   B('2001:1800::/23',    'ARIN',                                      '',                  '2003-04-01'),
#   B('2001:1a00::/23',    'RIPE NCC',                                  '',                  '2004-01-01'),
#   B('2001:1c00::/22',    'RIPE NCC',                                  '',                  '2004-05-04'),
#   B('2001:2000::/19',    'RIPE NCC',                                  '',                  '2019-03-12'),
#   B('2001:4000::/23',    'RIPE NCC',                                  '',                  '2004-06-11'),
#   B('2001:4200::/23',    'AFRINIC',                                   '',                  '2004-06-01'),
#   B('2001:4400::/23',    'APNIC',                                     '',                  '2004-06-11'),
#   B('2001:4600::/23',    'RIPE NCC',                                  '',                  '2004-08-17'),
#   B('2001:4800::/23',    'ARIN',                                      '',                  '2004-08-24'),
#   B('2001:4a00::/23',    'RIPE NCC',                                  '',                  '2004-10-15'),
#   B('2001:4c00::/23',    'RIPE NCC',                                  '',                  '2004-12-17'),
#   B('2001:5000::/20',    'RIPE NCC',                                  '',                  '2004-09-10'),
#   B('2001:8000::/19',    'APNIC',                                     '',                  '2004-11-30'),
#   B('2001:a000::/20',    'APNIC',                                     '',                  '2004-11-30'),
#   B('2001:b000::/20',    'APNIC',                                     '',                  '2006-03-08'),
#   B('2003:0000::/18',    'RIPE NCC',                                  '',                  '2005-01-12'),
#   B('2400:0000::/12',    'APNIC',                                     '',                  '2006-10-03'),
#   B('2600:0000::/11',    'ARIN',                                      '',                  '2006-10-03 see /12 -> /11'),
#   B('2610:0000::/23',    'ARIN',                                      '',                  '2005-11-17'),
#   B('2620:0000::/23',    'ARIN',                                      '',                  '2006-09-12'),
#   B('2630:0000::/12',    'ARIN',                                      '',                  '2019-11-06'),
    B('2800:0000::/12',    'LACNIC',                                    '',                  '2006-10-03'),
#   B('2a00:0000::/11',    'RIPE NCC',                                  '',                  '2006-10-03 see /12 -> /11'),
#   B('2a10:0000::/12',    'RIPE NCC',                                  '',                  '2019-06-05'),
#   B('2c00:0000::/12',    'AFRINIC',                                   '',                  '2006-10-03'),

    #  Address                                                                               Allocation  Termination                                    Globally   Reserved-by
    #  Block                Name                                         RFC                 Date        Date         Source  Destination  Forwardable  Reachable  Protocol
    # Special global-unicast addresses reserved for IETF protocols
    B('2001::/23',         'IETF Protocol Assignments',                 'RFC2928',          '2000-09',   '',          False,  False,       False,       False,     False),
    B('2001::/32',         'Teredo',                                    'RFC4380 RFC8190',  '2006-01',   '',          True,   True,        True,        None,      False),
    B('2001:1::1',         'Port Control Protocol Anycast',             'RFC7723',          '2015-10',   '',          True,   True,        True,        True,      False),
    B('2001:1::2',         'Traversal Using Relays around NAT Anycast', 'RFC8155',          '2017-02',   '',          True,   True,        True,        True,      False),
    B('2001:2::/48',       'Benchmarking (never route)',                'RFC5180 RFC1752',  '2008-04',   '',          True,   True,        True,        False,     False),
    B('2001:3::/32',       'AMT',                                       'RFC7450',          '2014-12',   '',          True,   True,        True,        True,      False),
    B('2001:4:112::/48',   'AS112-v6',                                  'RFC7535',          '2014-12',   '',          True,   True,        True,        True,      False),
    B('2001:20::/28',      'ORCHIDv2',                                  'RFC7343',          '2014-07',   '',          True,   True,        True,        True,      False),

    # This next one is very strange.  It's a reserved address for use in
    # documentation as a dummy address, like 555- phone numbers, and is never
    # to be assigned to a device.  The odd thing is that it is in the middle
    # of a public allocation to APNIC.  Why here and not in an unassigned
    # portion of the global unicast space?
    B('2001:db8::/32',     'Documentation (never route)',               'RFC3849',          '2004-07',   '',          False,  False,       False,       False,     False),

    B('2002::/16',         '6to4',                                      'RFC3056',          '2001-02',   '',          True,   True,        True,        None,      False),

    B('2620:4f:8000::/48', 'Direct Delegation AS112 Service',           'RFC7534',          '2011-05',   '',          True,   True,        True,        True,      False),

    # Some more reserved blocks in the global space
    B('2d00:0000::/8',     'IANA Reserved',                             '',                  '1999-07-01'),
    B('2e00:0000::/7',     'IANA Reserved',                             '',                  '1999-07-01'),
    B('3000:0000::/4',     'IANA Reserved',                             '',                  '1999-07-01'),
    B('3ffe::/16',         '6bone Testing Allocation',                  'RFC2471 RFC3701',   '1998-09',  '2006-06',   True,   True,        True,        True,      True ),
    B('5f00::/16',         '6bone Testing Allocation',                  'RFC2471 RFC3701',   '1996-03',  '1998-09',   True,   True,        True,        True,      True ),

    #---------------------------------------------------------------------------
    # Specific allocations that we see frequently enough
    # TODO Make sure 'WhoIs' works as well for each of these, and drop them
    #      as they're unmaintainable.
    B('2001:4860::/32',           'Google IPv6 (ASN 15169)'),
#   B('2001:578::/30',            'NETBLK-COXIPV6 (ASN 22773)'),
#   B('2600:3C00::/28',           'Linode US (ASN 63949)'),
#   B('2600:8800::/28',           'Cox Communications (CXA, ASN 22773)'),
#   B('2600:8802::/33',           'NET6-OC-RES-2600-8802-0000-0000 (ASN 22773)'),
#   B('2601::/20',                'COMCAST6NET (CCCS, ASN 7922)'),
#   B('2601:240::/26',            'CHICAGO-RPD-V6-2 (Fi, ASN 7922)'),
#   B('2606:4700::/32',           'Cloudflare Net (ASN 13335)'),
#   B('2607:F8B0::/32',           'GOOGLE-IPV6 (ASN 15169)'),
#   B('2620:fe::/48',             'PCH Public Resolver (quad9, ASN 19281)'),
    B('2600:6C00::/24',           'Charter Communications (CC04)'),
    B('2600:6c42:7003:300::/64',  'Tr Charter external (ASN 20115)'),
    B('2600:6c42:7600:1194::/64', 'Tr Charter internal (ASN 20115)'),

))

IP4 = IP4.address_block
ULA = ULA.address_block
GUA = GUA.address_block

#-------------------------------------------------------------------------------

def dump(short_addresses):
    wa = (max(len(str(block.address_block)) for block in allocations) if short_addresses else 39) + 5

    rfcs  = dict()
    stack = []
    for block in allocations:
        add_rfc(rfcs, block)
        addr = block.address_block
        while stack and not stack[-1].supernet_of(addr):
            stack.pop()
        stack.append(addr)

        indent = '| ' * (len(stack) - 1)
        if short_addresses:
            range  = str(addr)
        else:
            range  = addr.exploded.replace('0000', args.zero).split('/')
            range  = f'{range[0]}/{range[1]:>3}'
        fill = ' ' if len(stack) > 1 else '.'
        print(f'{range+" ":{fill}<{wa}} {indent}{block.name:{50-2*len(stack)}}  {block.rfc}')

    if args.verbosity > 0:
        show_rfcs(rfcs)

    raise SystemExit


def add_rfc(rfcs, block):
    if block.rfc:
        for rfc in set(block.rfc.split()):
            if rfc in rfcs and block.name not in rfcs[rfc]:
                rfcs[rfc].append(block.name)
            else:
                rfcs[rfc] = [block.name]

def show_rfcs(rfcs):
    if rfcs:
        print('Reference:')
        for rfc, ref in sorted(rfcs.items()):
            print(f'http://www.rfc-editor.org/rfc/{rfc.lower()} - {", ".join(ref)}')

#-------------------------------------------------------------------------------

def host(ipv6):
    """ Extract the host identifier from a full address. """
    return ipv6.exploded[20:][:19]

def subset(label, address, prefix, length):
    """ Given an address, extract the 'length' bits, starting a 'prefix'
        and build two strings.  If your prefix or length don't fall on 4-bit
        boundaries, well, it'll be ugly.

        >>> subset('GID', ip, 8, 40)
        ('GID: aa:6193:884e (40-bits)', 'fdaa:6193:884e::/48')
    """
    id = address.exploded
    l = f'{label}: xxx'

#-------------------------------------------------------------------------------

if __name__ == '__main__':
    args = parse_args()

    short_addresses = args.format == 'short'

    if args.list:
        dump(short_addresses)

    wa = (max(len(a) for a in args.addresses) if short_addresses else 39) + 5
    w2 = max(len(a.name) for a in allocations) + 8 # 8 is for depth fudging.

    rfcs = dict()

    for address in args.addresses:
        try:
            address = ipaddress.IPv6Interface(address)
        except Exception as exc:
            print(f'Invalid IPv6 address: {address!r}.  {exc}')
            continue

        if address in GUA:
            add_org(address.with_prefixlen)
            w2 = max(len(a.name) for a in allocations) + 8 # 8 is for depth fudging.

        addr   = str(address) if short_addresses else address.exploded.replace('0000', args.zero)
        prefix = f'{addr+" ":.<{wa-1}}'

        if not args.exploded:
            level  = 0
            for block in allocations:
                if address in block.address_block:
                    add_rfc(rfcs, block)
                    name = block.name + (f' [{block.rfc}]' if args.verbosity > 0 and block.rfc else '')
                    print(f'{prefix:{wa}}{"| "*level+name+" ":.<{w2}}', block.address_block)
                    prefix = ''
                    level += 1

            if address in ULA:
                id = address.exploded
                gid = 'GID: ' + id[ 2:14] + ' (40-bits) ' # Global ID
                sid = 'SID: ' + id[15:19] + ' (16-bits) ' # Subnet ID
                print(f'{prefix:{wa}}{"| "*level+gid:.<{w2}}', id[:15]+':/48')
                print(f'{prefix:{wa}}{"| "*level+sid:.<{w2}}', id[:20]+':/64')

            if True: #address._prefixlen <= 64:
                # Show the isolated Interface ID.
                iid = 'IID: ' + host(address) + ' (64-bits) '
                print(f'{prefix:{wa}}{"| "*level+iid:.<{w2}}', address.exploded)

            if level == 0:
                print(f'{prefix:43} Address in unknown or unassigned block')

        else:
            def binary(ipv6):
                # TODO fix this mess
                n = ipv6._ip
                b = ''
                h = ''
                for _ in range(8):
                    part = n & 0xFFFF
                    n >>= 16
                    b = f'{part:019_b} ' + b
                    h = '   ' + '    '.join(d for d in f'{part:04x}') + ':' + h
                return b[:-1] + '\n' + h[:-1]

            def bars(n_bits, start=0, c='|', ofs=0):
                # TODO fix this mess
                v = ''
                d = ' '
                for i in range(n_bits):
                    if i >= start:
                        d = '-' if c == '|' else ' '
                    if i and i % 4 == 0:
                        v += ' ' if i%8 == 0 else d
                    v += ' ' if start and i < start else c
                if 0: return v  # Are the bars more readable, or the digits?

                vv = ''
                ofs = 5*ofs // 4
                for c1, c2 in zip(v, bin_rep[ofs:], strict=False):
                    if c2 == '0': c2 = args.zero[0]
                    if c1 == '|':
                        vv += c2
                    else:
                        vv += c1
                return vv


            print(prefix, f'Scope: {address.scope_id}' if getattr(address,'scope_id', None) is not None else '')
            bin_rep = binary(address)

            pl = 0
            for block in allocations:
                if address in block.address_block:
                    add_rfc(rfcs, block)
                    l = block.address_block.prefixlen - pl
                    print(bars(block.address_block.prefixlen, pl), f'<{l}-bit: '+block.name + (f' [{block.rfc}]' if args.verbosity > 0 and block.rfc else ''), block.address_block)
                    pl = block.address_block.prefixlen

            if address in ULA:
                # SID mapping is wrong for ULAs (maybe others), see
                # 'Characteristics of IPv6 Unique Local Addresses (ULAs)' in
                # https://blogs.infoblox.com/ipv6-coe/3-ways-to-ruin-your-future-network-with-ipv6-unique-local/
                # Maybe this is right, but we're still missing the /8 bit.
                print(bars(48, start=8), '<40-bit: GID (Global ID)')
                print(bars(64, start=48), '<16-bit: SID (Subnet ID)')
            elif pl <= 64:
                print(bars(64, start=pl), f'<{64-pl}-bit: SID (Subnet ID)')
#           print(bars(64), '<64-bit: NID (Network ID)')

            print(bin_rep)
            if pl == 0:
                print(f'{address} Address in unknown or unassigned block')
            elif pl <= 64:
                print(bars(64-24, c=' '), '  64-bit: IID (Interface ID)>', bars(64, ofs=64))
            elif address in IP4:
                ip4 = ipaddress.IPv4Address(address._ip & 0xFFFF_FFFF)
                print(bars(128-19-32, c=' '), '32-bit: embedded IPv4>', bars(32, ofs=96))
                print(bars(pl, c=' '), '.'.join(f'{b:>9}' for b in str(ip4).split('.')))

        eui64_mask = 0x00FF_FE00_0000
        if (address._ip & eui64_mask) == eui64_mask:
            # Extract the MAC, toggle the
            hi = (address._ip & 0xFFFF_FF00_0000_0000) >> 40
            hi = hi ^ 0b0000_0010_0000_0000_0000_0000
            lo = (address._ip & 0x0000_0000_00FF_FFFF)
            nn = (hi << 24) + lo
            mac = ':'.join(f'{i:02x}' for i in nn.to_bytes(6, 'big'))

            try:
                # arp_data.json is a mapping from ether MAC addresses to host
                # name/description, looks like:
                # {
                #     "devices": {
                #         "00:15:5d:10:a8:2e": "host description",
                #         ...
                #     }
                # }
                input = open(os.path.join(install_dir, 'arp_data.json'))
            except OSError:
                # print('Could not find MAC -> hostname mappings')
                site_data = {}
            else:
                with input:
                    site_data = json.load(input)['devices']
            mfg  = '='.join(mac2mfg(mac))
            host_id = '('+site_data.get(mac, 'unknown host') + f'; {mfg})'
            eui_64  = address.exploded[20:-4]
            if args.exploded:
                print(bars(128-23-64, c=' '), f'64-bit: EUI-64> {eui_64} EUI-48 MAC>', mac, host_id)
                print()
            elif level != 0:
                level += 1
                print(f'{prefix:{wa}}{"| "*level+"EUI64: RFC4291 Appendix A ":.<{w2}}', eui_64)
                print(f'{prefix:{wa}}{"| "*level+"EUI48: Device MAC ":.<{w2}}', mac, host_id)

    if args.verbosity > 1:
        show_rfcs(rfcs)

#-------------------------------------------------------------------------------
