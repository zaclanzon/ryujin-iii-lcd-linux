# Package names are shared by the installer and scheduled Linux CI.
# Recheck official distro repositories when a scheduled job fails.
linux_family() {
    for lp_candidate in "$1" $2; do
        case "$lp_candidate" in
            ubuntu|pop|linuxmint|elementary|neon) echo ubuntu; return ;;
            debian) echo debian; return ;;
            fedora|rhel|centos|rocky|almalinux|nobara) echo fedora; return ;;
            arch|manjaro|endeavouros|cachyos) echo arch; return ;;
            opensuse*|suse|sles) echo suse; return ;;
            alpine) echo alpine; return ;;
            void) echo void; return ;;
        esac
    done
    return 1
}
linux_packages() {
    case "$1" in
        ubuntu|debian) printf '%s\n' 'python3 python3-venv python3-pip libusb-1.0-0 udev' ;;
        fedora) printf '%s\n' 'python3 python3-pip libusbx systemd-udev' ;;
        arch) printf '%s\n' 'python python-pip libusb systemd' ;;
        suse) printf '%s\n' 'python3 python3-pip libusb-1_0-0 udev' ;;
        *) return 1 ;;
    esac
}
linux_as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}
# Tumbleweed mirrors are briefly inconsistent while they sync, so a single
# refresh can fail on a missing repodata file. Retry, then let install refresh.
zypper_refresh() {
    zr_attempt=1
    while [ "$zr_attempt" -le 3 ]; do
        linux_as_root zypper --non-interactive --gpg-auto-import-keys refresh --force && return 0
        echo "zypper refresh failed (attempt $zr_attempt/3); retrying in 15s." >&2
        zr_attempt=$((zr_attempt + 1))
        sleep 15
    done
    echo 'zypper refresh kept failing; continuing so install can refresh itself.' >&2
    return 0
}
linux_dependencies() {
    [ "$(uname -s)" = Linux ] || { echo 'Linux is required.' >&2; return 1; }
    [ -r /etc/os-release ] || { echo 'Cannot detect the Linux distribution.' >&2; return 1; }
    . /etc/os-release
    lp_family=$(linux_family "${ID:-}" "${ID_LIKE:-}") || lp_family=unknown
    lp_packages=$(linux_packages "$lp_family") || {
        echo "No automatic package adapter for ${PRETTY_NAME:-unknown}. Use your host's native dependency tools." >&2
        return 1
    }
    printf 'Distribution: %s\nPackages: %s\n' "${PRETTY_NAME:-$lp_family}" "$lp_packages"
    if [ -e /run/ostree-booted ] || [ -d /ostree/repo ]; then
        echo 'Immutable host: configure these packages with the host tools, then skip package installation.' >&2
        return 1
    fi
    if [ "$lp_family" = arch ]; then
        echo 'Arch installs include a full upgrade to avoid partial upgrades.'
    fi
    [ "${LINUX_SETUP_DRY_RUN:-0}" != 1 ] || return 0
    # Word splitting is intentional: these are fixed package identifiers above.
    case "$lp_family" in
        ubuntu|debian) linux_as_root apt-get update
            linux_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $lp_packages ;;
        fedora) linux_as_root dnf --refresh install -y $lp_packages ;;
        arch) linux_as_root pacman -Syu --needed --noconfirm $lp_packages ;;
        suse) zypper_refresh
            linux_as_root zypper --non-interactive install $lp_packages ;;
        alpine) linux_as_root apk add --no-cache $lp_packages ;;
        void) linux_as_root xbps-install -Syu $lp_packages ;;
    esac
}
