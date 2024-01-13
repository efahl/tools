#!/usr/bin/env python3
# vim: set expandtab softtabstop=4 shiftwidth=4:
# Copyright (C) 2023-2024 Eric Fahlgren
# SPDX-License-Identifier: GPL-2.0
#-------------------------------------------------------------------------------
"""
Python implementation of the well-known 'whois' function, using availabel RIR
RDAP databases.
"""
#-------------------------------------------------------------------------------

from requests import get

args = None

#-------------------------------------------------------------------------------

class WhoIs:
    """ Query a collection of RIRs using their RDAP databases to find various
        attributes of a given address or subnet.

        Input 'address' can be one of three forms:

          - An IPv4 or IPv6 address or subnet, with or without a CIDR size
            specification.

          - An ARIN 'handle' like 'NET-68-0-0-0-1' or 'NET6-2601-240-1'.

          - A raw 'http[s]' reference to an RDAP entry:
              https://rdap.arin.net/registry/ip/2601:240::
            such as might be found in the raw output from a 'NET' lookup.

        >>> w = WhoIs('8.8.8.8')
        >>> print(w.owner)
        GOGL - Google LLC

        >>> w = WhoIs('2620:fe::fe/48')
        >>> print(w.handle)
        NET6-2620-FE-1

        >>> w = WhoIs('NET6-2620-FE-1')
        >>> print(w.name)
        PCH-PUBLIC-RESOLVER

        The ARIN docs seem to be the most complete:
            https://www.arin.net/resources/registry/whois/rdap/#rdap-urls

        There's no LACNIC in 'rdap_urls', as their documentation sucks and it
        looks like you need tokens.

        'rdap_urls' is sorted in order of my perceived reliability, although
        arin does fail more for some RIPE prefixes, for example, try 2a00::/11
    """

    rdap_urls = (
        'https://rdap.arin.net/registry/ip/',
        'https://rdap.db.ripe.net/ip/',
        'https://rdap.apnic.net/ip/',
        'https://rdap.afrinic.net/rdap/ip/',
    )

    def __init__(self, address):
        self._js     = None
        self.error   = None
        self.owner   = None
        self.handle  = None
        self.cidr    = None
        self.name    = None
        self.country = None
        self.asn     = None
        self.parent  = None

        if address.startswith('http'):
            self.from_ref(address)
        elif address.startswith('NET'):
            self.from_handle(address)
        else:
            self.from_ip(address)
        if args and args.verbose and self._js:
            import pprint
            print(f'--- {address=} ---')
            pprint.pprint(self._js)

    def __str__(self):
        return f'{self.name}: {self.owner} in {self.cidr}'

    def from_handle(self, handle):
        js = self._js_from_handle(handle)
        if js:
            if args and args.verbose:
                print(f'--- {handle=} ---')
                import pprint
                pprint.pprint(js)

            ref = js['net']['rdapRef']['$']
            if ref.endswith(':'):
                ref += '/' + js['net']['netBlocks']['netBlock']['cidrLength']['$']
            self.from_ref(ref)
            if handle == self.parent:
                # Happens for Linode: 2600:3c03::f03c:92ff:fe41:3428/64
                try:
                    self.parent = js['net']['parentNetRef']['@handle']
                except KeyError:
                    self.parent = None

    def from_ip(self, ip_or_subnet):
        """ Use the provided ip to search the various RDAP entries. """
        for rdap_url in WhoIs.rdap_urls:
            self.from_ref(f'{rdap_url}{ip_or_subnet}')
            if self.error is None:
                break

    def from_ref(self, url):
        """ From a given direct reference url, grab the json and go. """
        self.url   = url
        self.error = None
        with get(self.url) as response:
            if response.status_code == 200:
                self.extract(response.json())
            else:
                self.error = response.status_code

    def extract(self, js):
        self._js      = js
        self.owner    = self._org()
        self.handle   = self._js.get('handle',  'no-handle')
        self.cidr     = self._cidr()
        self.name     = self._js.get('name',    'no-name')
        self.country  = self._js.get('country', 'no-country')
        self.asn      = self._asn()
        self.parent   = self._parent()

    def __bool__(self):
        return self._js is not None
    __hash__ = None

    def _js_from_handle(self, handle):
        """ When an address starts with 'NET', then its net information may be
            fetched from ARIN via their Restful Web Services (RWS) site.

            Example:
            >>> curl -s http://whois.arin.net/rest/net/NET6-2601-240-1.json | json_pp
            >>> start  = js['net']['netBlocks']['netBlock']['startAddress']['$']
            >>> length = js['net']['netBlocks']['netBlock']['cidrLength']['$']
            >>> ref    = js['net']['rdapRef']['$']

            The 'rdapRef' is a link to the RDAP entry, but is missing the CIDR
            spec, so we append it.

            Reference:
            https://www.arin.net/resources/registry/whois/rws/api/
            Note that the /asn/ RWS might be of interest.
        """

        url = f'https://whois.arin.net/rest/net/{handle}.json'
        with get(url) as response:
            if response.status_code == 200:
                return response.json()

        self.error = response.status_code
        return None

    def _org(self):
        """ Search each entity for one with 'kind=org', then return its 'fn' name. """
        entities = self._js.get('entities')
        if entities is None:
            return 'no-organization'

        found = False
        organization = 'no-organization'
        for entity in entities:
            handle = entity['handle']
            for item in entity['vcardArray'][1]:
                if item[0] == 'fn':
                    organization = item[3]
                if item[0] == 'kind' and item[3] == 'org':
                    found = True
            if found:
                break

        return f'{handle} - {organization}'

    def _cidr(self):
        cidr_block = self._js.get('cidr0_cidrs')
        if cidr_block is None:
            h = self._js.get('handle')
            if h and h.startswith('2'):
                # Total hack for LACNIC missing cidr block.
                return h
            return 'no-CIDR'
        cidr_block = cidr_block[0]
        prefix = 'v4prefix' if 'v4prefix' in cidr_block else 'v6prefix'
        return f'{cidr_block[prefix]}/{cidr_block["length"]}'


    def _asn(self):
        for key in self._js:
            if 'aut' in key and self._js[key]:
                asn = self._js[key]
                if isinstance(asn, int):
                    return str(asn)
                return asn[0]
        return None

    def _parent(self):
        for key in self._js:
            if 'parent' in key:
                return self._js[key]
        return None

#-------------------------------------------------------------------------------

class WhoIsCache:
    # TODO make WhoIsCache a singleton
    def __init__(self, file=None):
        self.cache = dict()

        if file is not None:
            self.file = file
        else:
            from sys import platform
            _is_windows = platform.startswith('win')
            del platform

        self.file = 'c:/temp/whois.db' if _is_windows else '/tmp/whois.db'
        self.read()

    def __iter__(self):
        return iter(self.cache)

    def items(self):
        return self.cache.items()

    def _canonical_key(self, key):
        """ Hacky way to canonicalize GUAs. """
        if key.startswith('2'):
            key = key.lower()
        return key

    def add(self, key, whois):
        if whois:
            key = self._canonical_key(key)
            self.cache[key] = whois

    def get(self, key):
        key = self._canonical_key(key)
        return self.cache.get(key, None)

    def read(self):
        try:
            f = open(self.file, 'rb')
        except IOError as exc:
            print(exc)
            pass
        else:
            with f:
                from pickle import load
                self.cache = load(f)

    def write(self):
        try:
            f = open(self.file, 'wb')
        except IOError as exc:
            print(exc)
            pass
        else:
            with f:
                from pickle import dump
                dump(self.cache, f)

    def flush(self):
        self.cache = dict()
        self.write()

#-------------------------------------------------------------------------------

_cache = None

def _load_cache():
    global _cache
    if _cache is None:
        _cache = WhoIsCache()  # TODO make WhoIsCache a singleton

def whois_from_cache(address, refresh=False):
    _load_cache()

    if address == '::/0':
        # Ignore null.
        return

    whois = _cache.get(address)
    if not whois or refresh:
        # print(f'Updating cache {address}...')
        whois = WhoIs(address)
        _cache.add(address, whois)
        _cache.write()
    return whois

def whois_cache_keys():
    _load_cache()
    return sorted(_cache)

#-------------------------------------------------------------------------------

if __name__ == '__main__':
    test_addresses = (
    #   '2001:0000:4136:e378:8000:63bf:3fff:fdd2',
    #   '2001:0002::6c:ab:a',
    #   '2001:0004:112::48',
        '2001:1200::/23',  # LACNIC
        '2001:4200::/23',  # AFRINIC
        '2001:4400::/23',  # APNIC
        '2001:db8:8:4::2:1',
        '2002:624:624::16',
        '2002:cb0a:3cdd:1::1',
        '2600:3c03::f03c:92ff:fe41:3428/64',
        '2600:8802:4200:59:4c59:11ed:5359:49f6/64',
        '2600:8802:4200:f:a5f9:9e68:58d7:1d63/64',
        '2620:4f:8000::112:112:48',
        '2620:fe::9/48',
        '2620:fe::fe/48',
        '2a00:ffff:ffff:ffff:ffff:ffff:ffff:ffff',
        '2a01:4f8:c0c:9e5b::1/64',
        '64:ff9b::8.8.8.8',
        '8.8.8.8',
        '2a03:b0c0:3:d0::1af1:1',
        'NET6-2600-3C00-1',
        'NET6-2001-C00-1',
    )

    def parse_args():
        from argparse import ArgumentParser, RawDescriptionHelpFormatter as Formatter

        parser = ArgumentParser(
            formatter_class=Formatter,
            add_help=True,
            epilog='''
                Examples:
                    whois 8.8.8.8 NET-8-0-0-0-1
                    whois NET6-2601-1
                    whois 2001:600::/24
            '''.replace('                ', ''),
        )
        parser.add_argument('-v', '--verbose', default=False,  action='store_true',         help='Print a bunch of extra debugging data.')
        parser.add_argument('-r', '--refresh', default=False,  action='store_true',         help='Reload the cache using a fresh lookup.')
        parser.add_argument('-d', '--dump',    default=False,  action='store_true',         help='Dump the cache and quit.')
        parser.add_argument('-t', '--test',    default=False,  action='store_true',         help='Set an option state.')
        parser.add_argument(                   default=list(), dest='addresses', nargs='*', help='List IP addresses, subnets and handles.')

        args = parser.parse_args()
        if args.addresses or args.test or args.dump:
            return args

        parser.print_help()
        parser.exit()

    args = parse_args()

    if args.dump:
        keys = whois_cache_keys()
        key_len = max(len(k) for k in keys)
        for key in keys:
            whois = whois_from_cache(key, args.refresh)
            print(f'{key:<{key_len}} - {whois}')
        raise SystemExit

    if args.test:
        args.addresses = test_addresses

    for ip in args.addresses:
        print(f'Results for {ip!r}:')
        whois = whois_from_cache(ip, args.refresh)
        if not whois:
            print(f'        Error: {whois.error}')
        else:
            print('        Query:  ', whois.url)
            print('        Owner:  ', whois.owner)
            print('        Handle: ', whois.handle)
            print('        CIDR:   ', whois.cidr)
            print('        Name:   ', whois.name)
            print('        Country:', whois.country)
            print('        ASN:    ', whois.asn)
            print('        Parent: ', whois.parent)

#-------------------------------------------------------------------------------
