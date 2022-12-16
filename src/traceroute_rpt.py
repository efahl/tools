#!/usr/bin/env python3
# vim: set expandtab softtabstop=4 shiftwidth=4:
#-------------------------------------------------------------------------------
"""
Disassemble traceroute output, showing all three packet responses formatted
so that you can actually decipher what the hell is going on.
"""
#-------------------------------------------------------------------------------

def parse_args():
    from argparse import ArgumentParser, RawDescriptionHelpFormatter as Formatter

    parser = ArgumentParser(
        formatter_class=Formatter,
        epilog='''
            'traceroute' sends three packets to the destination host, usually ICMP,
            with an increasing time-to-live (TTL).  Since TTL is initially shorter than
            the route to the destination host, we get responses from the intermediate
            hosts along the route telling us that TTL has expired at that point.
            Each responses at a given TTL may or may not come from the same host, due
            to load leveling at the routers (e.g., host A at hop 7 may send the packet
            to hosts B and C at hop 8, if the cost is the same).

            If an intermediate host does not respond, it may be due to any of a number
            of factors: the host may b unreachable, or it may be configured to ignore
            ICMP packets and so on.

            See lots of details at:
                http://www.exit109.com/~jeremy/news/providers/traceroute.html
            (Search for '!H' if you're specifically looking for host response failures.)
        '''.replace('            ', ''),
    )

#   parser.add_argument('-v', '--verbose', default=0,  dest='verbose', action='store_true', help='Make output verbose.')
    parser.add_argument(                   default='', dest='host',    action='store',      help='Host name to trace.')
    args = parser.parse_args()
    return args

#-------------------------------------------------------------------------------

if __name__ == '__main__':
    from execute import get_status_output as run_cmd

    args = parse_args()

    status, output = run_cmd(f'traceroute {args.host}')
    lines = output.split('\n')
    print(lines.pop(0))

    maxw = max(len(s) for s in output.split())  # Find longest string in output, assume its a host name.

    last_ttl = 0
    for line in lines:
        ttl, *times = line.split()
        t = iter(times)
        if times == ['*', '*', '*']:
            # Just ignore anything with zero responses, we're beyond the edge or no ICMP.
            continue

        ttl = int(ttl)
        if ttl != last_ttl+1:
            print(f'Unresponsive host starting at {last_ttl+1} hops.')
        last_ttl = ttl

        print(f'Hop: {int(ttl):2}')
        for pkt in 1, 2, 3:
            print(f'  Pkt {pkt}: ', end='')
            try:
                v = next(t)
            except StopIteration:
                break
            if v == '*':
                print('No response')
                continue

            if v.startswith('!'):
                print(f'{v!r} handling not implemented.  Probably gonna crash.')
                continue

            v2 = next(t)
            if v2 == 'ms': # Can't happen on first group.
                # We already have host and ip from previous group.
                rtt  = v        # Just grab round trip for this query.
            else:
                host = v        # We have the responding host name.
                ip   = v2       # And its IP address.
                rtt  = next(t)  # Round trip time for this host's response.
                next(t)         # Eat the 'ms'
                ip = ip.replace('(', '').replace(')', '')
            rtt = float(rtt)
            print(f'{host:{maxw}}  {ip:15}  {rtt:7.3f} ms')
            host, ip = 'same', ''

#-------------------------------------------------------------------------------
