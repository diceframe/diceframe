# DiceFrame Privacy Policy

Last updated: August 21, 2026
Effective date: August 21, 2026
Version: 1.2

This Policy explains data handling by the local DiceFrame application, the public plugin index, and DiceFrame Hub. DiceFrame is self-hosted software, so actual processing depends on the deployer’s providers, plugins, network exposure, and sharing choices.

**Optional usage statistics are off by default. A periodic heartbeat begins only after a user actively enables the switch and confirms. Refusal or withdrawal does not restrict local features. Plugin interactions and online-room creation are user-requested Hub interactions and are described separately.**

## 1. Data stored locally

DiceFrame commonly stores games, characters, lorebooks, rules, scenes, maps, conversation and processing logs; settings, plugin data, themes, caches, and update state; model and bot credentials; and Hub identity and preference data under the deployer-controlled `data` directory. Installation does not automatically upload this content to maintainers. Removing the application may not remove a host-mounted data directory.

## 2. Network processing

### Model providers

When AI generation is requested, DiceFrame sends the prompt and game context needed for that request to the provider configured by the user. Provider retention and use are governed by that provider and any proxy selected by the deployer.

### GitHub, updates, announcements, and repositories

Update checks, announcements, repository views, and plugin downloads request GitHub, mirrors, or author-configured hosts. Those servers can ordinarily observe IP address, time, User-Agent, and request path and process them under their own policies.

Browsing the public DiceFrame Hub catalog does not create an installation identity, although coarse network identifiers may be processed for security and abuse prevention.

### Plugin interactions and Hub installation identity

The first Hub action requiring identity—including plugin installation, a like, rating, report, or online-room creation—creates a random installation ID and local token. Hub stores a one-way token digest and does not request a name, email, or DiceFrame save. Depending on the action, DiceFrame may send plugin ID and version, artifact hash, random event ID, install outcome, like state, 1–5 star rating, preset rating tags, report reason code, and a coarse network digest made with a periodically rotated key. These fields support deduplication, requested interactions, aggregate counts, and abuse prevention.

These actions are triggered by the user and are separate from the optional heartbeat switch. For room creation, the installation ID supports per-installation rate limits, active-room limits, revocation, and blocking; save content is not sent with it. Clearing Hub identity requests deletion of the installation and linked interactions and removes the local token; retry is required if Hub is offline. Creating another room later may create a new identity, while source-network and global limits still apply.

### Optional heartbeat

The optional heartbeat is off by default. After active opt-in, DiceFrame sends approximately every six hours: application version, coarse platform (Windows, macOS, Linux, or unknown), a Hub time bucket, and a pseudonymous installation ID. It does not include game text, characters, lorebooks, plugin lists, model settings, keys, passwords, private chats, or log content. It can be disabled under Management → Settings → Advanced → DiceFrame Hub and privacy, stopping new local heartbeats immediately.

### Direct connection, signaling, and STUN

When a user actively creates or joins a direct-connect room, the client contacts the signaling service identified by the link code over HTTPS and WebSocket. Room creation uses the pseudonymous installation identity described above; a joining participant uses the room's one-time credential and does not need a Hub installation identity. The official DiceFrame Hub keeps the room, one-time token digests, and pending SDP/ICE signaling only during a handshake window of approximately five minutes. After setup, diagnostic or game data travels between participant devices over WebRTC DataChannel and is not relayed or stored by Hub.

Direct-connect gameplay carries only protocol-allowlisted game state, character creation, player actions, luck or payment decisions, away status, and edits to the participant's own character. The host client binds each one-time invitation to the player identity created by that participant and processes those operations through the host's local game engine and model configuration. Participant devices and the host therefore process the characters, public log, permitted private messages, map, and actions visible to those identities. The link code also contains the selected game identifier. Send it only to intended participants. DataChannel is protected by DTLS, but participating endpoints can still view data delivered to them.

For security, attack prevention, bans, and incident investigation, official Hub separately records source IP, time, room code, host or guest role, connection outcome, User-Agent, Origin, and limited signaling counts and byte totals for 190 days by default. It does not write authentication tokens, SDP/ICE bodies, or DataChannel game content to the security log. This processing is required for the direct-connect security function and is independent of optional usage statistics. Hub may reject new rooms or handshakes with a retry time when connection, CPU, memory, or storage protection thresholds are reached.

WebRTC sends the network candidates and reachable public addresses needed for direct connection to the other participant. Each client can independently select a listed third-party STUN option (currently Cloudflare, Metered, Nextcloud, or a multi-provider combination), use no STUN, or enter one or more third-party/self-hosted STUN URIs. When STUN is selected, the client contacts those services directly; they can ordinarily observe source IP, request time, and protocol metadata, while Hub does not proxy the STUN requests. A link code contains a one-time guest secret, game identifier, and the host's selected STUN URI list, but the recipient may choose other STUN services or no STUN locally. Without a relay, strict NAT can prevent connection when neither side has a reachable route.

### Plugins, bots, tunnels, and public rooms

Independent plugins, chat bots, reverse proxies, tunnels, and publicly shared rooms may process additional data. Deployers must review permissions and third-party policies and inform affected players.

## 3. Purposes and retention

Data is processed only as needed to run local games and user-selected model calls; provide updates, announcements, catalog, installs, likes, ratings, and reports; prevent duplicates and abuse; and, after opt-in, understand version and platform distribution.

Local data remains until the deployer removes or replaces it. Current Hub defaults retain raw download and heartbeat events for no more than 30 days, direct-connect security events for 190 days, clear coarse network digests from reports after 30 days, and remove installation identities after 24 months of inactivity. Direct-connect rooms and signaling are normally removed from Hub memory after completion, disconnection, or approximately five minutes. De-identified aggregate statistics may be retained longer. Likes, ratings, install state, and unresolved reports generally remain until the related action or identity is deleted or the service no longer needs them. The all-in-one deployment currently rotates local backups after 14 days by default; other deployers control their own backup periods. Legal, security, backup, or dispute requirements may require limited extensions.

## 4. Sharing, international transfer, and choices

Maintainers do not sell DiceFrame game content or Hub identities. Data is shared only to provide a user-selected function, comply with law, or protect security and legal rights. GitHub, model providers, mirrors, Cloudflare, Metered, Nextcloud, tunnel services, and user-entered STUN services may operate in other countries; users and deployers should review the location and policy of services they select.

Users can keep heartbeat off or withdraw consent, clear Hub identity, remove local data, stop using a provider or plugin, decline direct connection, use no STUN, choose a trusted/self-controlled signaling or STUN service, and request access, correction, deletion, or restriction for Hub data. Verification may require the installation token still held locally. A new identity is created only if a later plugin interaction or online-room creation requires one.

## 5. Security, children, and changes

DiceFrame restricts local token file permissions and requires HTTPS for non-local Hub connections. Hub stores token digests, rotates network-digest keys, allow-lists signaling fields, limits rates/connections/resources, minimizes fields, and exposes deletion. WebRTC DataChannel uses DTLS encryption. No local or internet system is perfectly secure; use strong passwords, HTTPS, current releases, trusted plugins, and backups.

DiceFrame is intended for users with full legal capacity. Minors should use it with guardian supervision. Deployers should not knowingly collect sensitive information from children under 14 through Hub or public rooms.

Material changes to purposes, data types, or sharing will update the version and trigger a new in-app confirmation. If translations conflict, the Simplified Chinese version controls.

## 6. Who controls data and how to contact

The person or organization controlling a DiceFrame device or server determines processing of local games, accounts, shared links, and server logs. For DiceFrame Hub at `api.diceframe.com`, the DiceFrame project maintainers are responsible for processing they actually operate. DiceFrame is an open-source collaboration and does not represent model providers, plugin authors, GitHub, Cloudflare, NapCat, or other platforms.

Privacy or deletion requests may be raised through [GitHub Issues](https://github.com/diceframe/diceframe/issues). Do not include installation tokens, API keys, access passwords, private chats, or full saves in a public Issue; maintainers can provide a safer follow-up method when verification is needed.

