#!/bin/sh
# vim: set expandtab softtabstop=4 shiftwidth=4:
#-------------------------------------------------------------------------------

# TODO add arg parsing...
log_all=true        # Log packages that are dependencies, not just independent ones.
inc_defaults=true   # Log the default packages, not just the user-installed ones.
keep=false          # Save intermediate files.
debug=true          # Print some diagnostics.

pkg_json=/tmp/pkg-platform.json
pkg_defaults=/tmp/pkg-defaults
pkg_depends=/tmp/pkg-depends
pkg_installed=/tmp/pkg-installed
pkg_user=./installed-packages
pkg_info=./installed-info

#-------------------------------------------------------------------------------

get_installed() {
    # List the installed packages, per opkg.

    opkg list-installed | awk '{print $1}' > $pkg_installed
}

get_defaults() {
    # Using data from the ASU server, build a file containing a list of those
    # packages that are in the base installation (i.e., "not user installed").
    #
    # SNAPSHOT WARNING!
    # This might fail miserably for SNAPHOT boxes that are well out of date,
    # as the contents of snaphot builds is neither versioned nor maintained
    # for any long period.  Packages may come or go, or be renamed...

    local url board target version board_data release
    url=$(uci get attendedsysupgrade.server.url)
    eval $(ubus call system board | jsonfilter \
            -e 'board=$.board_name' \
            -e 'target=$.release.target' \
            -e 'version=$.release.version')

    #https://github.com/openwrt/packages/blob/master/utils/auc/src/auc.c#L756
    if [ "$target" = 'x86/64' -o "$target" = 'x86/generic' ]; then
        board='generic'
    else
        board=$(echo $board | tr ',' '_')
    fi

    if [ "$version" = 'SNAPSHOT' ]; then
        release='snapshots'
    else
        release="releases/$version"
    fi
    board_data="$url/json/v1/$release/targets/$target/$board.json"

    $debug && echo "Fetching $board_data"
    wget -q -O $pkg_json "$board_data"
    {
        echo 'kernel'
        jsonfilter -i $pkg_json \
            -e '$.default_packages.*' \
            -e '$.device_packages.*'
    } | sort -u > $pkg_defaults
}


get_dependencies() {
    # Using data from opkg status, build a file with lines like:
    #     pkg:dep1:dep2:dep3:
    # such that all dependencies are both prefixed and suffixed with ':',
    # but the root package is not.

    awk -F': ' '
        /^Package:/ {
            if (package != "") {
                # A package without dependencies.
                printf "%s:\n", package;
            }
            package = $2;
        }
        /^Depends:/ {
            dout = $2;
            gsub(/(\([^\)]*\)|), /, ":", dout);
            printf "%s:%s:\n", package, dout;
            package = "";
        }
    ' /usr/lib/opkg/status | sort > $pkg_depends
}

depends() {
    # Given a package, return the list of packages it depends upon, i.e., those
    # packages that will be installed implicitly by 'opkg' dependency checking.

    local pkg="$1"
    awk -F':' '/^'"$pkg"':/ {$1 = ""; print}' $pkg_depends
}


what_depends() {
    # Given a package, return the list of packages that depend on it.  If the
    # result is empty, this is a top-level package that must be explicitly
    # installed.

    local pkg="$1"
    awk -F':' '/:'"$pkg"':/ {printf "%s ", $1}' $pkg_depends

    # Old alternatives:
    # 1) Super slow.
    #    deps=$(opkg whatdepends "$pkg" | awk '/^\t/{printf $1" "}')
    #
    # 2) Faster.
    #    deps=$(cd /usr/lib/opkg/info/ &&
    #        grep -lE "Depends:.* ${pkg}([, ].*|)$" -- *.control | awk -F'.' '{printf $1" "}'
    #    )
}

is_default() {
    # Return status if package is in the defaults for this device, i.e., it
    # will be present as part of the standard install.

    local pkg="$1"
    grep -q '^'"$pkg"'$' $pkg_defaults
}

is_installed() {
    # Return status if package is installed on this device.

    local pkg="$1"
    grep -q '^'"$pkg"'$' $pkg_installed
}

what_provides() {
    # If a package doesn't appear directly in the installed packages, look
    # and see if the package name is aliased.

    local pkg="$1"
    opkg whatprovides $pkg | awk '/^ / {print $1}'
}

#-------------------------------------------------------------------------------

rm -f $pkg_user
get_defaults
get_dependencies
get_installed

examined=0
while read -r pkg; do
    examined=$((examined + 1))
    printf '%5d - %-40s\r' "$examined" "$pkg"
    deps=$(what_depends "$pkg")
    suffix=''
    if is_default "$pkg"; then
        if ! $inc_defaults; then
            $debug && echo "Skipping default package: $pkg"
            continue
        fi
        suffix="#default"
    fi
    count=$(echo "$deps" | wc -w)
    if $log_all || [ "$count" -eq 0 ]; then
        printf '%s%s\t%s\n' "$pkg" "$suffix" "$deps" | sed 's/\s*$//' >> $pkg_user
    fi
done < $pkg_installed

wid=$(wc -L < $pkg_defaults)
echo 'Default package scan results:' > $pkg_info
while read -r pkg; do
    if ! is_installed "$pkg"; then
        aliased=$(what_provides "$pkg")
        if [ -z "$aliased" ] || ! is_installed "$aliased"; then
            printf 'Warning: %-*s - default package is not present\n' "$wid" "$pkg"
        else
            $debug && printf 'Package: %-*s - replaced/provided by %s\n' "$wid" "$pkg" "$aliased"
        fi
    fi
done < $pkg_defaults | tee -a $pkg_info


if $keep; then
    echo 'Keeping working files:'
    ls -lh "$pkg_json" "$pkg_defaults" "$pkg_depends" "$pkg_installed"
else
    rm $pkg_json
    rm $pkg_defaults
    rm $pkg_depends
    rm $pkg_installed
fi

n_logged=$(wc -l < $pkg_user)
printf 'Done, logged %d of %d entries in %s\n' "$n_logged" "$examined" "$pkg_user"

