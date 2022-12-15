#!/bin/sh
# vim: set expandtab softtabstop=4 shiftwidth=4:
#-------------------------------------------------------------------------------
#
# https://www.he.net/adm/ntp.html         - San Jose and Fremont, below, as I live in CA.
# https://tf.nist.gov/tf-cgi/servers.cgi  - Fort Collins with IPv6.
# https://www.cloudflare.com/time/
# https://developers.google.com/time
# https://www.pool.ntp.org/zone/us        - 2. is only one with IPv6 support.

HOSTS="
    2.pool.ntp.org
    2.us.pool.ntp.org
    clock.fmt.he.net
    clock.sjc.he.net
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
    alias scan="awk 'BEGIN {FS=\"/\"}; /round-trip.*max / {print \$4}; /(std-dev|mdev) /  {print \$5}'"
fi

get_averages() {
    # In other words, this won't work on windows.
    local avg

    for host in $HOSTS; do
        #avg=$($ping $host | awk 'BEGIN {FS="/"}; /round-trip.*max / {print $4}; /(std-dev|mdev) /  {print $5}')
        avg=$(ping $host | scan)
        echo "$avg ms - $host"
    done
}


get_averages | sort -n
