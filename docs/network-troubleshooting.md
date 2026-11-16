# Connecting the Kobo after a Wi-Fi network changes

The PC's `http://127.0.0.1:8787/health` address checks its own service. A Kobo
or phone must use the PC's Wi-Fi address; their `127.0.0.1` refers to themselves.
A successful localhost check does not verify Wi-Fi access from another device.

On November 16, both Kobo audio handoff and browsing failed to find the paired
PC. The Kobo still had `http://10.0.0.213:8787`, while the PC's new Wi-Fi address
was `http://192.168.68.55:8787`. The original discovery code only searched the
saved address's private `/24`, so it never reached the new network. The existing
pairing token verified against the current PC; re-pairing was unnecessary.

The shared Kobo discovery module now checks the saved address first. If that
fails, a UDP route lookup identifies the reader's current private IPv4 address
without sending a packet. Discovery interleaves candidates from the current and
previous private `/24` networks, at the existing port. It keeps the ten-second
scan deadline, with at most 32 pending sockets when checking both networks.
It accepts a server only after verifying the nonce-bound HMAC proof; discovery
requests never contain the pairing token. This does not search every subnet or
make a PC on another network reachable.

The updated discovery module and current server address were installed and
verified on the physical Libra 2, preserving its pairing token, queued reading
positions and browser/reader settings. The previous plugin was backed up.
Restart KOReader after safely ejecting/unplugging to load the update. Actual
post-update connections from the Kobo still require user confirmation.

Three real KOReader/LuaSocket regression checks cover a saved address on a
previous network, route-probe failure and rejection of a public source address.
The complete project unittest discovery command also passed. A live check
through the PC's Wi-Fi address verified the paired API and a browser PNG's
length and SHA256, without changing reading observations.

Android 0.3.1 still searches its saved private `/24`. If its server address
belongs to the old network, enter the PC's current Wi-Fi address in KoboSync,
keep the existing token and tap **Save and sync**. Both devices need to reach
the awake PC over home Wi-Fi. No router reservation or new firewall rule is
required for this configuration repair.
