#!/usr/bin/env bash

set -e

INTERFACE="lo"

case "$1" in
    enable)
        echo "Enabling network impairment on ${INTERFACE}: 100ms delay, 1% packet loss"

        sudo tc qdisc replace dev "$INTERFACE" root netem \
            delay 100ms \
            loss 1%

        sudo tc qdisc show dev "$INTERFACE"
        ;;

    disable)
        echo "Removing network impairment from ${INTERFACE}"

        sudo tc qdisc del dev "$INTERFACE" root 2>/dev/null || true

        sudo tc qdisc show dev "$INTERFACE"
        ;;

    status)
        sudo tc qdisc show dev "$INTERFACE"
        ;;

    *)
        echo "Usage: $0 {enable|disable|status}"
        exit 1
        ;;
esac