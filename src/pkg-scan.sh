#!/bin/sh
# Copyright (c) 2023-2024 Eric Fahlgren <eric.fahlgren@gmail.com>
# SPDX-License-Identifier: GPL-2.0
# vim: set expandtab softtabstop=4 shiftwidth=4:
# shellcheck disable=SC2039,SC2155  # "local" not defined in POSIX sh
#-------------------------------------------------------------------------------

set -o nounset  # Make all undefined refs into errors.

# By default, we now grab board and version data from downloads and FS, not
# sysupgrade server. The issue with FS is that the data is not stored in a
# versioned, stable API, but rather in a site-specific js file.
use_asu=false

# User options, see 'usage'.
list_all=false
list_pkgs=false
inc_defaults=false
inc_missing=false
check_failed=false
keep=false
verbosity=0

# Global variables
url_sysupgrade=$(uci -q get attendedsysupgrade.server.url || echo 'https://sysupgrade.openwrt.org')  # sysupgrade server base url
url_firmware='https://firmware-selector.openwrt.org'
url_downloads='https://downloads.openwrt.org'         # This should be in config, too.
url_overview="$url_sysupgrade/json/v1/overview.json"  # Static
url_config="$url_firmware/config.js"                  # Our source for available versions.
url_failure=''                                        # Composed in dl_failures

# Files used.
tmp_loc='/tmp/pkg-'
pkg_defaults="${tmp_loc}defaults.txt"
pkg_depends="${tmp_loc}depends.txt"
pkg_installed="${tmp_loc}installed.txt"

pkg_fail_html="${tmp_loc}failures.html"

pkg_bom_json="${tmp_loc}bom.json"
pkg_overview_json="${tmp_loc}overview.json"
pkg_config_js="${tmp_loc}config.js"
pkg_pkg_arch_json="${tmp_loc}packages-arch.json"
pkg_pkg_platform_json="${tmp_loc}packages-plat.json"
pkg_platform_json="${tmp_loc}platform.json"

pkg_user=./pkg-scan-installed.txt

#-------------------------------------------------------------------------------
#-- Output and logging.

_log_() {
    # Log a message from the command line, or piped from stdin.
    if [ -t 0 ]; then
        printf '%s\n' "$@"
    else
        while read -r line ; do
            printf '%s\n' "$line"
        done
    fi
}

log_error() {
    # Print the messages to stderr.
    _log_ "$@" >&2
    return 1
}

colorize() {
    local color="${2:-255;0;0}"  # Default is red.
    printf '\033[38;2;%sm%s\033[m' "$color" "$1"
}
ERROR="$(colorize 'ERROR:')"
WARN="$(colorize 'Warning:' '255;255;0')"

log() {
    # Write to stdout if message verbosity <= system verbosity.
    # level == 0 -> always write, no cli option set
    # level == 1 -> more verbose, cli -v
    # level == 2 -> very verbose, cli -v -v
    level="$1"
    if [ "$level" -le "$verbosity" ]; then
        shift
        _log_ "$@"
    fi
}

#-------------------------------------------------------------------------------
#-- Globals state values -------------------------------------------------------

dev_arch=''       # "x86_64" or "mipsel_24kc"   or "aarch64_cortex-a53", contained in pkg_platform_json
dev_target=''     # "x86/64" or "ath79/generic" or "mediatek/mt7622", from board file
dev_platform=''   # "generic" (for x86) or "tplink,archer-c7-v4" or "linksys,e8450-ubi"
dev_fstype=''     # "ext4" or "squashfs"
dev_sutype=''     # Sysupgrade type: combined, combined-efi, sdcard or sysupgrade

bld_ver_from=''   # Full version name currently installed: "SNAPSHOT" or "22.03.1"
bld_kver_from=''  # Kernel version that is currently running
bld_num_from=''   # Current build on device

bld_ver_to=''     # Full version name of target: "23.05.2" or "SNAPSHOT"
bld_kver_to=''    # Kernel version of target build, extracted from BOM
bld_num_to=''     # Build number from target
bld_date=''       # Build date of target

rel_branch=''     # Release branch name: "SNAPSHOT" or "21.07" or "23.05"
rel_dir=''        # ASU and DL server release branch directory: "snapshots" or "release/23.05.0"

collect_config() {
    # Collect system state, and set source and target versions.
    eval "$(ubus call system board | jsonfilter \
            -e 'dev_platform=$.board_name' \
            -e 'bld_kver_from=$.kernel' \
            -e 'dev_target=$.release.target' \
            -e 'bld_ver_from=$.release.version' \
            -e 'bld_num_from=$.release.revision' \
            -e 'dev_fstype=$.rootfs_type')"

    bld_ver_to="${1:-$bld_ver_from}"  # User can override: SNAPSHOT or 22.03.5.  NOTE: Resets global!

    #https://github.com/openwrt/packages/blob/master/utils/auc/src/auc.c#L756
    if [ "$dev_target" = 'x86/64' ] || [ "$dev_target" = 'x86/generic' ]; then
        dev_platform='generic'
        dev_sutype='combined'
    else
        dev_platform="${dev_platform//,/_}"
        dev_sutype='sysupgrade'
    fi

    if [ -d /sys/firmware/efi ]; then
        dev_sutype="${dev_sutype}-efi"
    fi

    if [ "$bld_ver_to" = 'SNAPSHOT' ]; then
        rel_dir='snapshots'
        rel_branch='SNAPSHOT'
    else
        rel_dir="releases/$bld_ver_to"
        rel_branch="${bld_ver_to%.**}"
    fi

#   if false; then
#       # An oddball test case, with 'sdcard' sutype.
#       dev_platform='compulab_trimslice'
#       dev_target='tegra/generic'
#       dev_fstype='squashfs'
#       dev_sutype='sysupgrade'
#   fi
}

#-------------------------------------------------------------------------------

download() {
    local url="$1"
    local dst_file="$2"
    local msg="${3:-$ERROR Could not access $url (server down?)}"

    rm -f "${dst_file:?}"
    log 2 "Fetching $url to $dst_file"
    if ! wget -q -O "$dst_file" "$url"; then
        log_error "$msg"
        return 1
    fi
    return 0
}

dl_board() {
    # Get the starting point for the target build.  Downloads is a superset of
    # the file on sysupgrade server.
    if $use_asu; then
        local url_platform="$url_sysupgrade/json/v1/$rel_dir/targets/$dev_target/$dev_platform.json"
    else
        local url_platform="$url_downloads/$rel_dir/targets/$dev_target/profiles.json"
    fi
    local msg="$ERROR Could not download platform json.  Checking that version-to is correct."

    if ! download "$url_platform" "$pkg_platform_json" "$msg" || [ ! -e "$pkg_platform_json" ]; then
        show_versions | log_error
        exit 1
    fi
}

dl_packages() {
    # Download the two package lists, they are
    #  1) Generic arch package list, containing most of the items:
    #     https://sysupgrade.openwrt.org/json/v1/snapshots/packages/x86_64-index.json    
    #     Contains "vim" and "auc" and "dnsmasq-full"...
    #
    #  2) Platform packages, built specifically for this platform:
    #     https://sysupgrade.openwrt.org/json/v1/snapshots/targets/x86/64/index.json
    #     Contains things like "grub2" on x86_64 and "kmod-*" for everything.

    local url_pkg_arch="$url_sysupgrade/json/v1/$rel_dir/packages/${dev_arch}-index.json"
    download "$url_pkg_arch" "$pkg_pkg_arch_json"

    local url_pkg_plat="$url_sysupgrade/json/v1/$rel_dir/targets/$dev_target/index.json"
    download "$url_pkg_plat" "$pkg_pkg_platform_json"
}

dl_overview() {
    # Overview is the collection of information about the branches and their releases.
    #
    # Note that auc uses branches.json instead.  Its content is all included
    # in overview.json at '$.branches', but we like overview as it has a few
    # more useful items.  It can be found at:
    #     url_branches="$url_sysupgrade/json/v1/branches.json"
    #
    # Uses global 'url_overview'

    if ! download "$url_overview" "$pkg_overview_json"; then
        exit 1
    fi
}

dl_config() {
    # Grab the js from the firmware selector containing its list of available
    # build versions.

    if ! download "$url_config" "$pkg_config_js"; then
        exit 1
    fi
}

dl_bom() {
    # Download the platform BOM.

    local prefix="openwrt-"
    [ "$bld_ver_to" = 'SNAPSHOT' ] || prefix="${prefix}${bld_ver_to}-"
    [[ "$bld_ver_to" =~ .*-SNAPSHOT ]] && prefix="$(echo "$prefix" | awk '{print tolower($1)}')${bld_num_to}-"
    local url_bom="$url_downloads/$rel_dir/targets/$dev_target/${prefix}${dev_target/\//-}.bom.cdx.json"

    local msg="$WARN Could not access BOM for $bld_ver_to, kernel version cannot be determined"
    download "$url_bom" "$pkg_bom_json" "$msg"
}

dl_failures() {
    # The failures html resides in odd, one-man-out URL locations:
    #     https://downloads.openwrt.org/snapshots/faillogs/mipsel_24kc/packages/
    #     https://downloads.openwrt.org/releases/faillogs-23.05/mipsel_24kc/packages/
    #
    # Sets global 'url_failure'

    if [ "$bld_ver_to" = 'SNAPSHOT' ]; then
        location='snapshots/faillogs'
    else
        location="releases/faillogs-${rel_branch}"
    fi
    url_failure="$url_downloads/$location/$dev_arch/packages/"

    local msg="No package build failures found for $bld_ver_to $dev_arch"
    download "$url_failure" "$pkg_fail_html" "$msg"
}

#-------------------------------------------------------------------------------

get_defaults() {
    # Using data from the ASU server, build a file containing a list of those
    # packages that are in the base installation (i.e., "not user installed").
    #
    # SNAPSHOT WARNING!
    # This might fail miserably for SNAPHOT boxes that are well out of date,
    # as the contents of snapshot builds is neither versioned nor maintained
    # for any long period.  Packages may come or go, or be renamed...
    #

    # We grab package arch from the json, not the machine, because we may
    # expand this someday to use for cross device checking (say, to check
    # updates for your Archer from your x86).
    eval "$(jsonfilter -i $pkg_platform_json -e 'dev_arch=$.arch_packages')"

    {
        echo 'kernel'
        jsonfilter -i $pkg_platform_json \
            -e '$.default_packages.*' \
            -e "\$.profiles['${dev_platform}'].device_packages.*"
    } | sort -u > $pkg_defaults
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

get_installed() {
    # Compile a list of the installed packages, per opkg.
    opkg list-installed | awk '{print $1}' > $pkg_installed
}

get_user_packages() {
    # Filter out all the non-top-level packages, and optionally remove all
    # the defaults at the same time.

    local pkg
    while read -r pkg; do
        local deps=$(what_depends "$pkg")
        local suffix=''
        if is_default "$pkg"; then
            if ! $inc_defaults; then
                echo "Skipping default package: $pkg" | log 2
                continue
            fi
            suffix="#default"
        fi
        local count=$(echo "$deps" | wc -w)
        if $list_all || [ "$count" -eq 0 ]; then
            printf '%s%s\t%s\n' "$pkg" "$suffix" "$deps" | sed 's/\s*$//' >> "$pkg_user"
        fi
    done < $pkg_installed
}

show_config() {
    # Collect information about the actual installation, current and target images.
    #
    # If needed, profiles.json resides at:
    #   "$url_downloads/$rel_dir/targets/$dev_target/profiles.json"
    # but I believe everything we need is already in platform.json

    if [ "$dev_sutype" = 'sysupgrade' ]; then
        # Might still be wrong, so find 'sdcard' vs 'sysupgrade'...
        jsonfilter -i $pkg_platform_json -e "\$.profiles['${dev_platform}'].images[*].type" \
            | grep -q 'sdcard' \
            && dev_sutype='sdcard'
    fi

    eval "$(jsonfilter -i $pkg_platform_json -e 'bld_num_to=$.version_code')"

    # Use the platform data for most target image data.
    local img_prefix img_file
    if $use_asu; then
        eval "$(jsonfilter -i $pkg_platform_json \
            -e 'bld_date=$.build_at' \
            -e 'img_prefix=$.image_prefix' \
            -e "img_file=\$.images[@.type='${dev_sutype}' && @.filesystem='${dev_fstype}'].name")"
    else
        eval "$(jsonfilter -i $pkg_platform_json \
            -e "bld_date=$.source_date_epoch" \
            -e "img_prefix=\$.profiles['${dev_platform}'].image_prefix" \
            -e "img_file=\$.profiles['${dev_platform}'].images[@.type='${dev_sutype}' && @.filesystem='${dev_fstype}'].name")"
            bld_date=$(date -d @${bld_date} -uIs)
    fi

    # Use the platform BOM as it appears to be the only file containing
    # the target kernel version.
    if ! dl_bom; then
        bld_kver_to="$(colorize 'unknown' '255;255;0')"
    else
        bld_kver_to=$(jsonfilter -i $pkg_bom_json -e '$[*][@.name = "kernel"].version')
    fi

    log 1 << INFO
        Board-name    $dev_platform
        Target        $dev_target
        Package-arch  $dev_arch
        Version-from  $bld_ver_from $bld_num_from (kernel $bld_kver_from)
        Version-to    $bld_ver_to $bld_num_to (kernel $bld_kver_to)
        Image-prefix  $img_prefix
        Root-FS-type  $dev_fstype
        Sys-type      $dev_sutype
        Image-file    $img_file
        Build-at      $bld_date

INFO
}

show_versions() {
    # Grab the ASU overview to get all the available versions, scan that
    # for version-to and report.
    #
    # Note that the firmware selector has a much more liberal notion of
    # available versions:
    #   conf_url='https://firmware-selector.openwrt.org/config.js'
    #   ucode -p "$(curl -s $conf_url | sed 's/^var //'); sort(keys(config.versions))"
    # or
    #   eval $(ucode -p "$(curl -s $conf_url | sed 's/^var //')" | jsonfilter -e 'versions=$.versions')

    if ! $use_asu; then
        dl_config
        eval "$(ucode -p "$(sed 's/^var //' $pkg_config_js)" | jsonfilter -e 'versions=$.versions')"
    else
        # Old way, but sysupgrade list is very short and has none of the older
        # builds which might still be of interest.
        dl_overview

        # shellcheck disable=SC2034  # 'latest' and 'branches' are unused, but may be someday.
        local latest branches versions
        eval "$(jsonfilter -i $pkg_overview_json \
                -e 'latest=$.latest.*' \
                -e 'branches=$.branches' \
                -e 'versions=$.branches[*].versions.*')"
    fi

    printf '\nValid version-to values from %s:\n' "$url_overview"
    {
        local found=false
        for rel in $versions; do
            if [ "$bld_ver_to" = "$rel" ]; then
                printf '- %s     <<-- your version-to is correct\n' "$rel"
                found=true
            else
                printf '- %s\n' "$rel"
            fi
        done
        if $found; then
            printf 'Either the ASU server is having issues, or your specified version is no longer supported.'
        else
            printf "Your selected version-to '%s' is invalid." "$(colorize "$bld_ver_to")"
        fi
    } | sort
}

#-------------------------------------------------------------------------------

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

what_provides() {
    # If a package doesn't appear directly in the installed packages, look
    # and see if the package name is aliased.
    # vim -> vim-full

    local pkg="$1"
    opkg whatprovides "$pkg" | awk '/^ / {print $1}'
}

provides_what() {
    # Inverse of above.  Given a versioned or variant package, find out what
    # its base package is named.
    # vim-full -> vim  or  libjson-script202312041 -> libjson-script
    local pkg="$1"
    opkg info "$pkg" | awk '/Provides:/ {print $2}'
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

in_packages() {
    # Search for a given package in both the platform and arch package lists.
    local pkg="$1"
    [ -n "$(jsonfilter -i $pkg_pkg_platform_json -e "\$.packages['${pkg}']")" ] && return 0
    [ -n "$(jsonfilter -i $pkg_pkg_arch_json     -e "\$['${pkg}']")"          ] && return 0
    # BUG alias can be a list of packages, e.g., libncurses
    local alias=$(provides_what "$pkg")
    [ -n "$alias" ] && [ "$alias" != "$pkg" ] && in_packages "$alias" && return 0
    return 1
}

#-------------------------------------------------------------------------------

check_defaults() {
    # Scan the package defaults to see if they are
    #    1) missing from the installation or
    #    2) modified/replaced by some other package.
    #
    # If you specify '-m', then the missing default packages will be included
    # in the "user installed" package list.  If you have manually removed them,
    # then '-m" will undo those removals.

    printf 'Default package analysis:\n'
    local widest=$(wc -L < $pkg_defaults)
    local issues=false
    while read -r pkg; do
        if ! is_installed "$pkg"; then
            local alias=$(what_provides "$pkg")
            if [ -z "$alias" ] || ! is_installed "$alias"; then
                # TODO check if it 'whatconflicts' with something that /is/ installed
                # e.g, libustream-mbedtls replaced by libustring-openssl
                issues=true
                printf "  %-*s - $WARN default package is not present\n" "$widest" "$pkg"
                if $inc_missing; then
                    printf '%s#missing\n' "$pkg" >> "$pkg_user"
                fi
            else
                issues=true
                printf '  %-*s - default package replaced/provided by %s\n' "$widest" "$pkg" "$alias"
            fi
        fi
    done < $pkg_defaults
    $issues || printf '  No missing or modified default packages.\n'
}

check_replacements() {
    # Look in overview.json for 'package_changes' to see if pkg has been
    # replaced.  E.g., migration from wolfssl in 22.03 to mbedtls in 23.05.
    #
    # Note that the current overview and branches.json files are broken,
    # with 'libustream-wolfssl' appearing twice in 'package_changes'.

    printf 'Installed packages replaced in %s:\n' "$bld_ver_to"

    local changes="$.branches['$rel_branch'].package_changes"
    local sources=$(jsonfilter -i $pkg_overview_json -e "${changes}[*].source" | sort -u)

    # Testing:
    #printf '%s\n' $sources >> $pkg_user
    #echo 'libustream-wolfssl' >> $pkg_user

    local changed=false
    for from in $sources; do
        if grep -q "^${from}\b" "$pkg_user"; then
            local to=$(jsonfilter -l1 -i $pkg_overview_json \
                -e "${changes}[@.source = '${from}'].target")
            printf '  %s -> %s\n' "$(colorize "$from")" "$to"
            changed=true
        fi
    done
    $changed || printf '  No changes detected.\n'
}

check_non_existent() {
    # Scan all the user packages for existence in the target's package lists.
    # TODO optionally remove missing packages from the user list.

    if [ ! -e "$pkg_pkg_platform_json" ]; then
        log_error "$WARN Cannot verify platform packages due to missing json."
        return
    fi
    if [ ! -e "$pkg_pkg_arch_json" ]; then
        log_error "$WARN Cannot verify architecture packages due to missing json."
        return
    fi

    printf 'Installed packages not available in %s:\n' "$bld_ver_to"

    #echo 'ima-fake-package' >> $pkg_user  # Testing
    local pkgs=$(awk -F'#|\t' '{printf "%s ", $1} END {printf "\n"}' "$pkg_user")

    local missing=false
    for pkg in $pkgs; do
        [ "$pkg" = 'kernel' ] && continue
        is_default "$pkg" && continue
        if ! in_packages "$pkg"; then
            printf '  %s\n' "$(colorize "$pkg")"
            missing=true
        fi
    done
    $missing || printf '  No issues, all packages present.\n'
}    

check_failures() {
    # Crude attempt at finding any build issues with the packages.  Scrapes
    # the html from the downloads status page.

    if dl_failures; then
        printf 'There are currently package build failures for %s %s:\n' "$bld_ver_to" "$dev_arch"

        # Testing!  Pick a package that's currently not building
        #p=cloudflared ; echo $p >> $pkg_installed ; echo $p >> $pkg_user

        # Scraping the html is a total hack.
        # Let me know if you have an API on downloads that can give this info.
        local bad_ones=$(awk -F'<|>' '/td class="n"/ {printf "%s ", $7}' < $pkg_fail_html)

        # shellcheck disable=SC2086  # Because we want to expand bad_ones.
        local widest=$(printf "%s\n" $bad_ones | wc -L)
        local found=false
        for bad in $bad_ones; do
            if grep -q "\b$bad\b" $pkg_installed; then
                msg=$(colorize 'ERROR: You have this installed, DO NOT UPGRADE!')
                found=true
            else
                msg='Package not installed locally, you should be ok'
            fi
            printf '  %-*s - %s\n' "$widest" "$bad" "$msg"
        done
        $found && printf "%s" "$(colorize 'NOTE THE ERRORS ABOVE: ')"
        printf 'Details at %s\n\n' "$url_failure"
    fi
}

#-------------------------------------------------------------------------------

usage() {
    echo "$0 [OPTION]...

Compile a report of all user-installed packages into '$pkg_user'.

  Package processing:
    -a, --all           Log packages that are dependencies, not just independent ones.
    -d, --defaults      Log the installed default packages, not just the user-installed ones.
    -m, --missing       Log the missing default packages along with user-installed.
    -V, --version-to V  Use 'V' as the current version instead of installed.
    -f, --failed        Check for failed package builds on intended version-to.

    -c, --check         Most common checks: enable all of -d -m -f -v

  Output:
    -o, --output F      Path to which scan results are written, default '$pkg_user'
    -k, --keep          Save and ls intermediate files.
    -l, --list          Display package list on a single line, appropriate for builder.
    -v, --verbose       Print various diagnostics.  Repeat for even more output.

'Version-from' is version and revision currently installed on this device.
'Version-to' is derived from downloaded platform.json file."

    exit 1
}

# Default to check if nothing specified.
[ "$#" -eq 0 ] && set -- "--check"

__check=false
__list=false
while [ "${1:-}" ]; do
    case "$1" in
        -a|--all     ) list_all=true ;;
        -d|--defaults) inc_defaults=true ;;
        -m|--missing ) inc_missing=true ;;
        -k|--keep    ) keep=true ;;
        -v|--verbose ) verbosity=$((verbosity + 1)) ;;
        -f|--failed  ) check_failed=true ;;
        -o|--output  )
            pkg_user=$2
            shift
        ;;
        -l|--list    )
            __list=true
            list_pkgs=true
        ;;
        -c|--check   )
            __check=true
            inc_defaults=true
            inc_missing=true
            check_failed=true
            verbosity=$((verbosity + 1))
        ;;
        -V|--version-to)
            shift
            bld_ver_to=$(echo "$1" | awk '{print toupper($1)}')
        ;;
        *)
            usage
        ;;
    esac
    shift
done

if $__check && $__list; then
    echo "The '-c/--check' and '-l/--list' options are mutually exclusive, pick one or the other."
    exit 1
fi

#-------------------------------------------------------------------------------

rm -f "${pkg_user:?}"

collect_config "$bld_ver_to"

dl_board

get_defaults
show_config
get_dependencies
get_installed
get_user_packages

log 1 "$(check_defaults)"
log 1 ''

if $check_failed; then
    log 0 "$(check_failures)"
    log 1 ''
fi

content=$(sort "$pkg_user") && echo "$content" > "$pkg_user" && unset -v content

dl_overview
log 1 "$(check_replacements)"
log 1 ''

# Checking for non-existent packages requires that the 'pkg_user' list be
# fully populated, so it comes last.
dl_packages
log 1 "$(check_non_existent)"

examined="$(wc -l < "$pkg_installed")"
n_logged="$(wc -l < "$pkg_user")"

if ! $keep; then
    rm -f ${tmp_loc:?}*
else
    log 1 ''
    log 1 'Keeping working files:'
    log 1 "$(ls -lh ${tmp_loc}*)"
fi

if ! $list_pkgs; then
    rm -f "${pkg_user:?}"
else
    printf 'Done, logged %d of %d entries in %s\n' "$n_logged" "$examined" "$pkg_user" | log 1
    log 1 ''
    awk -F'#|\t' '{printf "%s ", $1} END {printf "\n"}' "$pkg_user"
fi
