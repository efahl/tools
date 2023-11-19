#!/bin/sh
# Copyright (c) 2023 Eric Fahlgren <eric.fahlgren@gmail.com>
# SPDX-License-Identifier: GPL-2.0
# vim: set expandtab softtabstop=4 shiftwidth=4:
# shellcheck disable=SC2039  # "local" not defined in POSIX sh
#-------------------------------------------------------------------------------

# User options, see 'usage'.
list_all=false
list_pkgs=false
inc_defaults=false
inc_missing=false
check_failed=false
keep=false
verbose=false
quiet=false
version_to=''

# Global variables
version=''        # "SNAPSHOT" or "22.03.1"
package_arch=''   # "x86_64" or "mipsel_24kc" or "aarch64_cortex-a53"

# Files used.
pkg_json=/tmp/pkg-platform.json
pkg_fail=/tmp/pkg-failures.html
pkg_defaults=/tmp/pkg-defaults
pkg_depends=/tmp/pkg-depends
pkg_installed=/tmp/pkg-installed
pkg_user=./installed-packages
pkg_info=./installed-info

usage() {
    echo "$0 [OPTION]...

Compile a report of all user-installed packages into '$pkg_user',
and list anything anomalous in '$pkg_info'.

  Package processing:
    -a, --all       Log packages that are dependencies, not just independent ones.
    -d, --defaults  Log the installed default packages, not just the user-installed ones.
    -m, --missing   Log the missing default packages along with user-installed.
    --version-to V  Use 'V' as the current version instead of installed.
    -f, --failed    Check for failed package builds on intended version.

  Output:
    -k, --keep      Save and ls intermediate files.
    -l, --list      Display package list on a single line, appropriate for builder.
    -v, --verbose   Print various diagnostics.
    -q, --quiet     Do not print any standard output.

    -x              Enable all of -d -m -k -l -f -v"
    exit 1
}

while [ "$1" ]; do
    case "$1" in
        -a|--all     ) list_all=true ;;
        -d|--defaults) inc_defaults=true ;;
        -m|--missing ) inc_missing=true ;;
        -k|--keep    ) keep=true ;;
        -v|--verbose ) verbose=true ;;
        -q|--quiet   ) quiet=true ;;
        -l|--list    ) list_pkgs=true ;;
        -f|--failed  ) check_failed=true ;;
        -x           )
            inc_defaults=true
            inc_missing=true
            keep=true
            list_pkgs=true
            check_failed=true
            verbose=true
        ;;
        --version-to)
            shift
            version_to="$1"
        ;;
        *)
            usage
        ;;
    esac
    shift
done

#-------------------------------------------------------------------------------

get_installed() {
    # Compile a list of the installed packages, per opkg.

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
    #
    # Globals defined here:
    #     version      - "SNAPSHOT" or "22.03.1"
    #     package_arch - "x86_64" or "mipsel_24kc" or "aarch64_cortex-a53"

    local url          # sysupgrade server base url
    local board_data   # synthesized url of board json
    local board        # "generic" (for x86) or "tplink,archer-c7-v4" or "linksys,e8450-ubi"
    local target       # "ath79/generic" or "mediatek/mt7622" or "x86/64"
    local release      # "snapshots" or "release/23.05.0"
    local fstype       # "ext4" or "squashfs"

    url=$(uci get attendedsysupgrade.server.url)
    eval "$(ubus call system board | jsonfilter \
            -e 'board=$.board_name' \
            -e 'target=$.release.target' \
            -e 'version=$.release.version' \
            -e 'fstype=$.rootfs_type')"

    version=${1:-$version}  # User can override: SNAPSHOT or 22.03.5.  NOTE: Resets global!

    #https://github.com/openwrt/packages/blob/master/utils/auc/src/auc.c#L756
    if [ "$target" = 'x86/64' ] || [ "$target" = 'x86/generic' ]; then
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

    if $verbose; then
        echo "Fetching $board_data to $pkg_json"
    fi
    wget -q -O $pkg_json "$board_data"

    # We grab package arch from the json, not the machine, because we may
    # expand this someday to use for cross device checking (say, to check
    # updates for your Archer from your x86).
    eval "$(jsonfilter -i $pkg_json -e 'package_arch=$.arch_packages')"

    {
        echo 'kernel'
        jsonfilter -i $pkg_json \
            -e '$.default_packages.*' \
            -e '$.device_packages.*'
    } | sort -u > $pkg_defaults

    if $verbose; then
        echo "Board-name    $board"
        echo "Target        $target"
        echo "Version       $version"
        echo "Root-FS-type  $fstype"
        local b p
        eval "$(jsonfilter -i $pkg_json -e 'b=$.build_at' -e 'p=$.image_prefix')"
        echo "Image-prefix  $p"
        echo "Build-at      $b"
    fi
}


get_dependencies() {
    # Using data from opkg status, build a file containing all installed
    # package dependencies.  Each line appears thus:
    #
    #     pkg:dep1:dep2:dep3:
    #
    # such that 
    #     pkg = an installed package, with no prefixing delimiter
    #     dep = all dependencies are both prefixed and suffixed with ':'

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
            gsub(/ \([^\)]*\)/, "", dout);  # Remove version spec.
            gsub(/, /, ":", dout);          # Convert separators.

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
    opkg whatprovides "$pkg" | awk '/^ / {print $1}'
}

#-------------------------------------------------------------------------------

check_failures() {
    # Crude attempt at finding any build issues with the packages.  Scrapes
    # the html from the downloads status page.
    # https://downloads.openwrt.org/snapshots/faillogs/mipsel_24kc/packages/
    # https://downloads.openwrt.org/releases/faillogs-23.05/mipsel_24kc/packages/

    if [ "$version" = 'SNAPSHOT' ]; then
        location='snapshots/faillogs'
    else
        location=$(echo "$version" | awk -F'.' '{printf "releases/faillogs-%s.%s", $1, $2}')
    fi
    url="https://downloads.openwrt.org/$location/$package_arch/packages/"

    if wget -q -O $pkg_fail "$url"; then
        {
            echo ''
            echo "There are currently package build failures for $version $package_arch:"
            bad_ones=$(awk -F'<|>' '/td class="n"/ {printf "%s ", $7}' < "$pkg_fail")
            for bad in $bad_ones; do
                if grep "\b$bad\b" $pkg_installed; then
                    msg='you have this installed, DO NOT UPGRADE'
                else
                    msg='package not installed locally, you should be ok'
                fi
                printf "  %-28s - %s\n" "$bad" "$msg"
            done
            echo "Details at $url"
            echo ''
        } >> "$pkg_info"
    fi
}

#-------------------------------------------------------------------------------

rm -f $pkg_user
get_defaults "$version_to"
get_dependencies
get_installed

examined=0
while read -r pkg; do
    examined=$((examined + 1))
#   ! $quiet && printf '%5d - %-40s\r' "$examined" "$pkg"
    deps=$(what_depends "$pkg")
    suffix=''
    if is_default "$pkg"; then
        if ! $inc_defaults; then
            $verbose && echo "Skipping default package: $pkg"
            continue
        fi
        suffix="#default"
    fi
    count=$(echo "$deps" | wc -w)
    if $list_all || [ "$count" -eq 0 ]; then
        printf '%s%s\t%s\n' "$pkg" "$suffix" "$deps" | sed 's/\s*$//' >> $pkg_user
    fi
done < $pkg_installed

wid=$(wc -L < $pkg_defaults)
while read -r pkg; do
    if ! is_installed "$pkg"; then
        aliased=$(what_provides "$pkg")
        if [ -z "$aliased" ] || ! is_installed "$aliased"; then
            printf 'Warning: %-*s - default package is not present\n' "$wid" "$pkg"
            if $inc_missing; then
                printf '%s#missing\n' "$pkg" >> $pkg_user
            fi
        else
            $verbose && printf 'Default: %-*s - replaced/provided by %s\n' "$wid" "$pkg" "$aliased"
        fi
    fi
done < $pkg_defaults > $pkg_info

if $check_failed; then
    # This appends to $pkg_info...
    check_failures
fi

if ! $quiet; then
    echo ''
    cat $pkg_info
fi

if $keep; then
    if ! $quiet; then
        echo 'Keeping working files:'
        ls -lh "$pkg_json" "$pkg_defaults" "$pkg_depends" "$pkg_installed" "$pkg_fail"
    fi
else
    rm $pkg_json
    rm $pkg_defaults
    rm $pkg_depends
    rm $pkg_installed
    [ -e $pkg_fail ] && rm $pkg_fail
fi

content=$(sort "$pkg_user") && echo "$content" > $pkg_user

n_logged=$(wc -l < $pkg_user)
! $quiet && printf 'Done, logged %d of %d entries in %s\n' "$n_logged" "$examined" "$pkg_user"

if $list_pkgs; then
    $verbose && echo ''
    awk -F'#|\t' '{printf "%s ", $1} END {printf "\n"}' installed-packages
fi
