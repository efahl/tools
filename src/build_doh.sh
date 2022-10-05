#!/bin/sh
# vim: set expandtab softtabstop=4 shiftwidth=4:
#-------------------------------------------------------------------------------

if [ ! -x /usr/bin/resolveip ] ; then
    # Create a 'resolveip' function that takes an IP version and domain name,
    # resolves and returns all of the IP addresses for the domain in that
    # family.  'resolveip' already exists on OpenWrt as an optional package
    # ('opgk install resolveip'), so we can skip this.

    function resolveip() {
        # Call with 'resolveip -4 google.com', like the OpenWrt equivalent
        # 'resolveip ${ipv} ${host}'

        ipv=$1   # -4 or -6
        host=$2  # fqdn

        # 'dig' returns unrequested CNAMES, which are always FQDNs ending
        # with '.', so we remove those from the return values.
        [ $ipv == '-4' ] && rr=A || rr=AAAA
        dig $host $rr +short | grep -v '\.$'
    }
fi

#-------------------------------------------------------------------------------

function build_stats() {
    # Build a closure for a 'stats' command that translates an IP address into
    # its previous counter statistics.

    json=$(nft -j list set inet fw4 doh_ipv${1})

    echo 'function stats() {'
    echo '  local stats'
    echo '  case $1 in'
    echo $json | jsonfilter -e '@.nftables[1].set.elem[*].elem' | while read line; do
        eval $(jsonfilter -s "$line" -e 'ip=@.val' -e 'packets=@.counter.packets' -e 'bytes=@.counter.bytes')
        if [[ $bytes != 0 || $packets != 0 ]] ; then
            echo "    $ip) stats=' counter packets $packets bytes $bytes' ;;"
        fi
    done
    echo '    *) stats="" ;;'
    echo '  esac'
    echo '  echo "$stats"'
    echo '}'
}

#-------------------------------------------------------------------------------

# Another utility to download files.  On OpenWrt use 'uclient-fetch', assume
# 'curl' is installed elsewhere.
[ -x /bin/uclient-fetch ] && alias get='uclient-fetch -q -O -' || alias get='curl -s'
hosts=$(get "https://raw.githubusercontent.com/dibdot/DoH-IP-blocklists/master/doh-domains.txt")


for ipv in 4 6 ; do
    build_stats $ipv > ./fn  # Make the stats command that returns stats='packets n bytes m'
    . ./fn
    rm ./fn

    cat <<EOF >./v${ipv}.tmp
#!/bin/sh

# Note that if you change parameters of the set, you'll have to delete and
# re-add the set, as its header structure is static once completed.
#
# DELETE ONLY WORKS IF NO RULES ARE ATTACHED.  You'll see this:
# Error: Could not process rule: Resource busy
# delete set inet fw4 doh_ipv4
#                     ^^^^^^^^
# So:
# nft -a list ruleset | grep doh_ipv${ipv}
# nft delete rule inet fw4 dstnat_lan handle XXX

# nft delete set inet fw4 doh_ipv${ipv}
# nft add set inet fw4 doh_ipv${ipv} { \\
#     typeof ip${ipv%4} daddr \; \\
#     counter \; \\
#     gc-interval 1h \; \\
#     timeout 25h \; \\
# }
# nft add rule inet fw4 dstnat_lan \\
#     meta l4proto { tcp, udp } th dport { 80, 443 } \\
#     ip${ipv%4} daddr @doh_ipv${ipv} \\
#         counter \\
#         accept/reject or whatever


nft flush set inet fw4 doh_ipv${ipv}

nft add element inet fw4 doh_ipv${ipv} {\\
EOF

    for host in $hosts ; do
        echo ${ipv} $host
        for ip in $(resolveip -${ipv} $host) ; do
            echo "    $ip$(stats $ip), \\" >> ./v${ipv}.tmp
        done
    done
    echo '}' >> ./v${ipv}.tmp
done

