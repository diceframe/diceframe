# Player Direct Connect (Experimental)

Player Direct Connect is intended for small groups that do not want to deploy a separate public DiceFrame server. The host creates a temporary room in their own DiceFrame and issues a one-time link code to each friend. Once connected, game requests travel through a WebRTC data channel to the host. Saves, model configuration, rules, and GM processing remain under the host's control.

DiceFrame Hub participates only in the brief connection handshake. It does not proxy game payloads or store campaign narration. Hosts should share link codes only with players they know and trust.

## Join friends from the homepage

The regular invitation link is the default way to join a friend. The host enters the play page, clicks “Invite friends”, and copies the generated invitation link. A guest can open that link directly or return to the homepage and paste it into “Join friends”. The QR code remains available for scanning in the DiceFrame app, or the regular link can be opened on a supported device.

Regular invitation links usually contain game information in a form such as `#/join?game=...`. They enter the public Web play flow and keep the existing seat credentials, character identity binding, and access checks. Guests do not need to extract a room ID, player ID, or other fields from the link, and they do not need to configure a model. `DFP2-...` is an advanced direct-connect code and cannot be opened as a regular invitation link.

Hosts should use the advanced path only when they need experimental client direct connect: from “Join friends” on the homepage, open “More connection methods” and choose “Start direct connect (experimental)”. This creates a temporary direct-connect room and codes to share. Direct-connect codes and regular invitation links have different purposes; the host must remain online and authoritative for a direct-connect room.

## Inviting existing characters and new players

The direct-connect setup page shows the save's character seats. One click creates the room and generates every available P2P link code as a batch:

- Every existing character automatically receives an identity-bound link code. Connecting restores that character without creating another character or consuming another seat.
- Every free seat automatically receives a new-player link code. It opens character creation and consumes the seat only after character creation; an unsent or unused code consumes no save seat.
- Each direct-connect link code may use only the existing character bound to it. A generic new-player code cannot claim another occupied character.
- The batch size is constrained by both the save capacity and the DiceFrame Hub per-room peer limit.
- Public Web play can still use the “web control link” beside a character. The host can copy it again whenever it is lost; it is different from the five-minute direct-connect link code.

A full save may therefore still create a direct-connect room when it has existing non-host characters that need to rejoin.

## Before using it

- Link codes are single-use and expire after five minutes.
- The host must keep DiceFrame and the direct-connect room online. Leaving the room ends the room and its game connections.
- Symmetric NAT, strict firewalls, corporate networks, or browsers that disable WebRTC may prevent a connection.
- The experimental transport forwards an explicit set of JSON game operations. Binary attachments, speech-recognition audio uploads, and some custom media are not transferred; the UI hides those entry points or falls back to bundled assets.
- Direct connect is not a replacement for an unattended public server or a long-lived hosted room.

## Permissions and privacy

The host remains authoritative. Each connected peer is mapped to the identity bound by the system or created through that link and may use only whitelisted operations such as joining, submitting an action, reading that player's private information, resolving that player's confirmations, and editing that player's character. Extra client-supplied fields are stripped, and requests are rate- and concurrency-limited.

Player actions, character data, and game requests reach the host device and may be processed by the model endpoint configured by the host. Participants should understand both the host and model-provider privacy boundaries before joining.

## Effect on normal development

Direct connect is an optional adapter. It does not change the dice, rules, lorebook, save, or round-processing core. When no peer session is active, or when a request does not belong to the active peer game, the frontend continues through the existing HTTP/SSE path.

Normal source development therefore still needs only:

```powershell
python web_server.py

cd frontend-v2
npm run dev
```

Developing local Web UI, LAN play, bots, rules, or content packs does not require DiceFrame Hub or WebRTC configuration. A reachable rendezvous Hub and two browser endpoints are needed only when testing direct connect itself.

The code boundary is intentionally narrow:

- Backend Hub routes only obtain rendezvous configuration and create temporary rooms.
- Frontend protocol, session, and game bridge code is grouped under `frontend-v2/src/peer/`.
- `frontend-v2/src/api/client.ts` contains one nullable request interception point; without an active peer client it immediately falls back to the normal network request.
- The host bridge calls the existing local game API instead of duplicating the dice or rules engine.

## Developer regression tests

```powershell
cd frontend-v2
npm run test
npm run typecheck
npm run build
```

Direct-connect protocol, invitations, game-operation whitelists, identity recovery, and inactive-peer HTTP fallback have focused tests. Run `npm run test:e2e` as well when changing connection UI or real browser collaboration.
