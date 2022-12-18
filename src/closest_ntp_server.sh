#!/bin/sh
# vim: set expandtab softtabstop=4 shiftwidth=4:
#-------------------------------------------------------------------------------
# Use 'ping' to calculate average rtt to NTP hosts.  We're operating under
# the assumption that low round-trip time translates to fewer hops translates
# to the least jitter translates to best NTP server.  Yeah, not exactly
# scientific, but probably good enough.
#
# Tested on
#     OpenWrt
#     OPNsense
#     Windows Cygwin, Windows Git Bash
#     Debian, Ubuntu, Fedora
#
# Sources of host lists:
# https://www.he.net/adm/ntp.html  - 'sjc' = San Jose and 'fmt' = Fremont.
# https://tf.nist.gov/tf-cgi/servers.cgi
# https://www.cloudflare.com/time/
# https://developers.google.com/time
# https://www.pool.ntp.org/zone/us - '2.hosts' are the only ones with IPv6 support.
#
# Only hosts supporting IPv6 were included in the list below.
#-------------------------------------------------------------------------------

HOSTS="
    2.pool.ntp.org
    2.us.pool.ntp.org
    clock.fmt.he.net
    clock.sjc.he.net
    ntp.he.net
    time-d-wwv.nist.gov
    time-e-wwv.nist.gov
    time.cloudflare.com
    time.google.com
"

n_hosts=$(echo $HOSTS | wc -w)
est_time=$(($n_hosts * 5))
echo "Be patient, pinging $n_hosts hosts should take about $est_time seconds..."


if [ "$WINDIR" ] ; then
    # Windows 'ping' summary line looks like:
    #     Minimum = 32ms, Maximum = 40ms, Average = 35ms
    alias ping='ping -n 4'
    alias scan="grep Average | sed 's/.*Average = \(.*\)ms.*/\1/'"
else
    # Relies on 'ping' producing a summary line of the form:
    #     round-trip min/avg/max = 10.575/11.674/13.911 ms               - OpenWrt/BusyBox
    #     round-trip min/avg/max/std-dev = 10.139/12.944/19.168/3.642 ms - OPNsense/FreeBSD
    #     rtt min/avg/max/mdev = 34.767/36.802/41.440/2.718 ms           - Most Linux
    alias ping='ping -c4 -q'
    alias scan="awk 'BEGIN {FS=\"/\"}; /max = / {print \$4}; /(std-dev|mdev) = /  {print \$5}'"
fi

get_averages() {
    local avg
    for host in $HOSTS; do
        avg=$(ping $host | scan)
        echo "    $avg ms - $host"
    done
}


data=$(get_averages | sort -n)
echo -e '\033[1A\033[2KHosts by lowest RTT:'
echo "$data"

if hash uci 2>/dev/null; then
    echo ''
    echo 'Current settings:'
    uci show system.ntp

    echo ''
    echo 'Consider setting your ntpd servers on this OpenWrt device by running:'
    echo ''
    echo 'uci show system.ntp'
    echo 'uci delete system.ntp.server'
    echo "$data" | head -4 | sed 's/.* /uci add_list system.ntp.server=/'
    echo 'uci show system.ntp'
    echo 'uci commit'
    echo '/etc/init.d/sysntpd restart'
    echo "ps www | grep '\\bntp  '"
fi
