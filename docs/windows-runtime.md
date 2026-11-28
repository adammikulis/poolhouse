# Windows runtime

Poolhouse runs on Windows itself. The installer puts the app and its daemon on the computer; no WSL,
Python or developer tool is needed.

## What the installer does

`Poolhouse_<version>_x64-setup.exe` installs for the current user. It installs the WebView2 runtime when
Windows lacks it. Run over an older copy, it stops what runs from that install and replaces it; state in
`%USERPROFILE%\.poolhouse` is kept.

Windows blocks inbound connections until a firewall rule allows them, and only an administrator adds
one. The installer asks once. The rules cover TCP 8770 (the daemon), TCP 7447 and UDP 7448 (the node and
its discovery) and TCP 7450 (linked phones), on private networks only. Declined, the install goes on: this
computer still reaches its pool by connecting out, but other devices cannot connect to it or find it.
A network Windows calls Public gets no open port; set the network to Private in Settings.

## What runs

The window starts `poolhouse-headless.exe`, which is the daemon and its web page on
`http://127.0.0.1:8770/ui/`. The node (`poolhouse-node.exe`) ships inside it and runs under a supervisor
of its own. A model server is a llama.cpp Windows release (CUDA where there is an NVIDIA GPU),
downloaded and checked against its pinned digest the first time it is needed.

State and caches are in the Windows user profile. `POOLHOUSE_HOME` moves state.

## What is not confined on Windows

A model server on Windows is **not sandboxed**. On macOS and Linux the sandbox takes its network away and
limits what it can read; Windows has no equivalent here yet. What holds on Windows:

- it listens on loopback only and answers no request without its per-start key;
- another device reaches it only through the daemon's authenticated proxy, which passes pictures, sound
  and files only as bytes in the request, never as a path or an address;
- a job object bounds its memory, and ends it with its starter when it is leased that way;
- the key files and Poolhouse's state are readable by this account (and the computer's administrators).

A flaw in the server's handling of a model file, a picture or a prompt would run with this account's
rights, with its files and network.

Coding agents do not start on native Windows: their network cannot yet be confined to the model.
