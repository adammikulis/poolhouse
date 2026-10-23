# Mac preserved work ledger
This is the Mac response to the missing-source request in [the cross-device preservation ledger](native-preserved-work.md). It publishes recorded identities and custody metadata through Git. It grants no execution authority, pool membership, cleanup permission or K acceptance. The devices do not yet have an authenticated pool/API connection; a Git note does not establish one.

## Evidence checkpoints

The original read-only inventory was taken at **2026-10-23T18:04:43.020224+00:00**, against `origin/0.3dev` = `90c120c389cfdeaac3423bf11f7a7f23e85217a7` on macOS 26.6 build 25G70, arm64. It recorded 54 existing registered worktrees, 191 refs, 20 worktrees with unique commits, and 14 verified external bundles. Every original tree status command succeeded. These are original observations, not a refreshed global inventory.

This ledger was assembled on 2026-10-23T22:39:52.833670+00:00 from the retained inventory and later task-specific closure receipts. Its Git comparison checkpoint is `d0b276b1f594aaa79d72eb319c8584eb985d52f6`. Original refs, dirty bytes, ignored assets and bundles were not reset, rewritten, archived again or globally cleaned for this handoff. Later closures below cover only explicitly identified worker/review checkouts.

Local evidence handle **E-MAC** resolves on the Mac to the retained inventory directory. The following SHA-256 values bind the original metadata files; their absolute paths, historical attribution and private configuration contents are not published here. They are metadata snapshots, not dirty-byte backups.

| Evidence handle | Bytes | SHA-256 |
| --- | --- | --- |
| `E-MAC/summary.json` | 1340 | `d4e5ad89bd962a3e5f3f0a7241ce46f38df0d40a1d36a07bae97ee2ace53a53d` |
| `E-MAC/worktrees.json` | 16351300 | `006392227ed892bb5dc1d32dc0da9ae242a47b3d31cee62b24e8c0d628e1e6cc` |
| `E-MAC/refs.json` | 42380 | `9e61a3d41d515f9316c05b156aba74135241adae7529d214df1663abf8b939f6` |
| `E-MAC/artifacts.json` | 12555451 | `8c96ae9499b8b39d47c78b854d7bd82aac32a0c59dd2dd68520d409f93e7e649` |
| `E-MAC/ignored-assets.json` | 73497 | `8c4df76978e20ba0d9c430fd8c7e2c5f12b74fc12ce50cfe87887b909dd8bf29` |
| `E-MAC/live-ownership.json` | 5793 | `dd8fe06e204df9aa40ecf26cfb0f59e4d28ed6e397ff2a5e0d214183e3ba0486` |

Custody handles: `repo/` is the Mac primary checkout; `repo-parent/` is its containing repository directory; `temporary-review/Mnn` is an opaque locator resolved by E-MAC. `H-MAC/` is the retained external history directory containing the named bundles. No handle authorizes access or transmits its bytes to another device.

## Original worktrees

Each full SHA is the original head. Counts `B/U` mean commits only on the original baseline / only on this head, from the recorded `rev-list --left-right --count baseline...head`. `U=0` establishes ancestry only, not behavior retention or a deletion decision. Unique commits remain pending source/behavior review. All rows retain original custody unless a later explicit closure below says otherwise; present-day state was not re-inventoried.

| ID | Original custody locator | Full original head | Ref ID | B/U | Tracked edits / untracked / ignored paths | Observed claim owner |
| --- | --- | --- | --- | --- | --- | --- |
| M01 | `repo` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | R001 | 0/0 | 0/0/65456 | not observed |
| M02 | `temporary-review/M02` | `83ae4c5b6d5526811b62b3f6978603f658ffa600` | detached | 147/6 | 0/0/800 | not observed |
| M03 | `temporary-review/M03` | `e2ef47c16771736a7ac871fb0993608c995c0197` | detached | 147/0 | 0/1/586 | not observed |
| M04 | `temporary-review/M04` | `3f85d82063d338e6903d607f7dbf943a322ee05c` | R097 | 147/14 | 0/0/1096 | not observed |
| M05 | `temporary-review/M05` | `b429acd9c3a25fafb655783b119409d0ee6cb7bf` | R099 | 147/13 | 0/0/639 | not observed |
| M06 | `repo-parent/poolhouse-claim-release` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | R052 | 0/0 | 0/1/390 | chatgpt-2fd7db |
| M07 | `repo-parent/poolhouse-codex-native-session` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | R047 | 0/0 | 0/0/390 | chatgpt-6512f3 |
| M08 | `repo-parent/poolhouse-l1` | `a86c509ed75c13971af1898cf5e1781edfd8818f` | R083 | 36/6 | 0/0/856 | not observed |
| M09 | `repo-parent/poolhouse-limits` | `41d07e10f9a6b8b75d978614a9a03c2ef0bcb289` | R054 | 12/148 | 0/0/1217 | not observed |
| M10 | `repo-parent/poolhouse-net-security` | `57df071e03984218dd942e0aee6940690bd394e3` | R063 | 64/126 | 0/0/1129 | not observed |
| M11 | `repo-parent/poolhouse-phone-free-1` | `529f81be4c8266fae8e2bac60f24cfd1ea7977f6` | R025 | 1141/7 | 0/0/51487 | not observed |
| M12 | `repo/.claude/worktrees/agent-a0807cc3cfdb68792` | `e5e4ccfe79c5c7ca12d721f3750189b5d78b4375` | R027 | 79/0 | 0/0/1202 | not observed |
| M13 | `repo/.claude/worktrees/agent-a16ad8c82d20f8bdb` | `11faf8036a31ff50474b6e472aab7da217e906b5` | R095 | 47/0 | 0/0/1672 | not observed |
| M14 | `repo/.claude/worktrees/agent-a1a4ff19a63605ab9` | `75318562353d9bfdd2468dfac6d7071ae6e925a2` | R100 | 147/7 | 0/0/395 | not observed |
| M15 | `repo/.claude/worktrees/agent-a203d5d8d24496b3b` | `36d6f3038f460254732dcc139ee19e265791da0c` | R050 | 180/0 | 0/0/1186 | not observed |
| M16 | `repo/.claude/worktrees/agent-a22020ad887e8c71f` | `e19e69a1f465e42fc84f3efb5814d10cbf26227a` | R111 | 322/0 | 0/0/2582 | not observed |
| M17 | `repo/.claude/worktrees/agent-a2788a150d2cac1b2` | `ae159f8ff2331b308499349983bab7bcd4aee1d7` | R064 | 64/13 | 25/0/1100 | not observed |
| M18 | `repo/.claude/worktrees/agent-a2b3a156ebb1260ed` | `450cd887b7960c3c600293a04485561372d8cdb1` | R104 | 147/11 | 0/0/853 | not observed |
| M19 | `repo/.claude/worktrees/agent-a3183ebf734b5941f` | `fb265362f256ef57bc4d033f27c5adbd41caa782` | R055 | 64/125 | 0/0/1324 | not observed |
| M20 | `repo/.claude/worktrees/agent-a35395b261e2a2887` | `6ffbb592ba1cb9b09119263c9e3f78bff60fb9d5` | R081 | 16/0 | 0/0/1670 | not observed |
| M21 | `repo/.claude/worktrees/agent-a35f5e8b0b356d73a` | `6780925157c65f98b1d1980e006cfca5365fa05e` | R053 | 207/0 | 0/0/642 | not observed |
| M22 | `repo/.claude/worktrees/agent-a40775dffa86f9bdd` | `863b331fc6fc129277896b00c73f449f58ffda3e` | R024 | 260/0 | 0/0/1219 | not observed |
| M23 | `repo/.claude/worktrees/agent-a4a9032b88bd2a2b2` | `bc1b7608f009bde06f783229f8cb598406015d8d` | R068 | 64/124 | 0/0/1771 | not observed |
| M24 | `repo/.claude/worktrees/agent-a534b7b0aff6abd95` | `f19f7026a304472349108345374a86345cd8bb18` | R085 | 45/0 | 0/0/1737 | not observed |
| M25 | `repo/.claude/worktrees/agent-a54688b962ef258e6` | `c36016bda84c7b410e5783134d4b875339591401` | R012 | 217/0 | 0/0/887 | not observed |
| M26 | `repo/.claude/worktrees/agent-a5b1d0120c46de98f` | `f4108781eccedf7c44fc11c1f16f174083e20558` | R057 | 84/79 | 0/0/1728 | not observed |
| M27 | `repo/.claude/worktrees/agent-a5c9d53004763f175` | `7c8743031360f21174a52530087d38c14278e3ef` | R107 | 236/0 | 0/0/1087 | not observed |
| M28 | `repo/.claude/worktrees/agent-a6c6170b92733a181` | `e399a1d4a21c26c05064367000fcafb93d4880cd` | R015 | 109/0 | 0/0/571 | not observed |
| M29 | `repo/.claude/worktrees/agent-a70cbd85b3ac372d9` | `229c969dae9b0725733a1350539de8dacfb06237` | R137 | 75/0 | 0/0/577 | not observed |
| M30 | `repo/.claude/worktrees/agent-a715c094d9cb75f02` | `724881eeb4bf3aafcc4853f6828563f288ff6539` | R003 | 97/0 | 0/0/1834 | not observed |
| M31 | `repo/.claude/worktrees/agent-a7674c3136345a527` | `a215dd7f0f8061c9f4a1be76a76e39008783a9cc` | R066 | 31/146 | 7/1/1137 | not observed |
| M32 | `repo/.claude/worktrees/agent-a76ff81511438e108` | `5b2c6f2becf049603679ab656a3cbef84ccd1178` | R072 | 333/0 | 0/0/972 | not observed |
| M33 | `repo/.claude/worktrees/agent-a81187afe6abeb7b2` | `2385a4b727fb5ac5c6007c9b93881b3cb696cf95` | R006 | 286/0 | 0/0/806 | not observed |
| M34 | `repo/.claude/worktrees/agent-a83d10f8e758ca40b` | `5a632cf0a667f4cc3826f716ad4d75c354d2770f` | R106 | 270/0 | 0/0/1117 | not observed |
| M35 | `repo/.claude/worktrees/agent-a92377c1e3e8d3318` | `44433d5548781004d02f5623a92e444ef3db117c` | R020 | 186/0 | 0/0/1221 | not observed |
| M36 | `repo/.claude/worktrees/agent-a94cb6b8f75525413` | `5ce43a17650b99df99f7afc80a315374fd6b2974` | R062 | 64/125 | 1/0/757 | not observed |
| M37 | `repo/.claude/worktrees/agent-a9597030a0397ce12` | `e97621879dac95bd7f70a17352a4f0ab8db82923` | R022 | 106/0 | 0/0/967 | not observed |
| M38 | `repo/.claude/worktrees/agent-aa1fc5bd7b924d738` | `ec2a13766899f32128483b1b67f0ee4ff6d14d4a` | R070 | 64/125 | 0/0/1177 | not observed |
| M39 | `repo/.claude/worktrees/agent-aa4b8c1dd7c889848` | `a308658101c2adc21615f88fcf17f7fb88ef949e` | R036 | 2/0 | 0/0/1663 | not observed |
| M40 | `repo/.claude/worktrees/agent-aa4d634b049d69d19` | `21978bfd6191ca446810adc4a7a3c5a55e15414d` | R026 | 279/0 | 0/0/635 | not observed |
| M41 | `repo/.claude/worktrees/agent-aa70e2df41234a1db` | `6f304962b2732afa8e40236c476e5c927e7cac32` | R004 | 249/0 | 0/0/757 | not observed |
| M42 | `repo/.claude/worktrees/agent-aa7afe968ba9f8a89` | `e8a40a53271403dd554fa239694c804f6a9db689` | R059 | 64/126 | 0/0/1773 | not observed |
| M43 | `repo/.claude/worktrees/agent-aac5b79bd43524675` | `143675439cade20cb43fec33e9c147ef7ca69b32` | R084 | 302/0 | 0/0/1004 | not observed |
| M44 | `repo/.claude/worktrees/agent-ac071ab204615561d` | `f7c06d4cf271a2c7fd2241558395e1ecf7d787e4` | R105 | 293/0 | 0/0/1045 | not observed |
| M45 | `repo/.claude/worktrees/agent-ac55104966fa2148a` | `291a7a7a979e71847258ec5bd34366f583a7b183` | R073 | 182/0 | 0/0/885 | not observed |
| M46 | `repo/.claude/worktrees/agent-ac77732d307031711` | `1fa732fb01788a7488fd0a6e751769b8c793bc9c` | R079 | 196/0 | 0/0/1172 | not observed |
| M47 | `repo/.claude/worktrees/agent-acd8343b1ad605217` | `bf17c71cd93cb47416a69d10a6381e2896afd82a` | R046 | 192/0 | 0/0/1038 | not observed |
| M48 | `repo/.claude/worktrees/agent-ad2c517480c4b4ee4` | `e7c30ff70bc1eca798ee3a321ebd7383d37bb857` | R101 | 64/14 | 0/0/21 | not observed |
| M49 | `repo/.claude/worktrees/agent-ae0075ec3e28bd382` | `529b957f38a46b8a486f780bf8206ff0e40378d2` | R168 | 152/0 | 0/0/954 | not observed |
| M50 | `repo/.claude/worktrees/agent-ae04626e1dec6608c` | `cd62703f93e2f4d127d5aa639935ad232b9ec252` | R042 | 219/0 | 0/0/983 | not observed |
| M51 | `repo/.claude/worktrees/agent-ae883f3a50f0f3cd0` | `40f290381f2b3f159ea44d8c510c92551c5fa209` | R060 | 64/140 | 0/0/1208 | not observed |
| M52 | `repo/.claude/worktrees/agent-aeaf0c0e05a2c3480` | `fdfeb35a2b262db1fe35640c5c1be9827a8b6772` | R010 | 95/0 | 0/0/577 | not observed |
| M53 | `repo/.claude/worktrees/agent-aec2b5c3d8c478a8f` | `be5020d918ba6380b016dd6b02e6b33a97bf6ff4` | R080 | 31/4 | 2/0/1243 | not observed |
| M54 | `repo/.claude/worktrees/agent-af4afa033bced0241` | `128b7e2c796297868e86b2b6c805855d7e425b95` | R023 | 140/0 | 0/1/932 | not observed |

Claim observations are the original point-in-time workspace response, never authority. “Not observed” does not mean abandoned. The two observed source-worker owners were `chatgpt-2fd7db` (M06) and `chatgpt-6512f3` (M07); their claim expiries were minutes, not a current lease. Later behavior/disposition ownership is unresolved unless explicitly mapped below.

## Original ref manifest

Every original ref and full object SHA follows. `B/U` uses the same original baseline comparison. A published SHA does **not** publish otherwise-unreachable Git objects. Ref ancestry is storage evidence; no ref is silently classified as obsolete or behavior-equivalent. Local refs with `U>0` require owner review and confirmed external custody before any future removal.

| ID | Original ref | Full SHA | B/U |
| --- | --- | --- | --- |
| R001 | `refs/heads/0.3dev` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | 0/0 |
| R002 | `refs/heads/build/studio-native-3d6c8ca` | `3d6c8ca264e638694be95f2ce6efa96ee5aa1d88` | 975/0 |
| R003 | `refs/heads/chore/affected-before-push` | `724881eeb4bf3aafcc4853f6828563f288ff6539` | 97/0 |
| R004 | `refs/heads/chore/grants-verify4` | `6f304962b2732afa8e40236c476e5c927e7cac32` | 249/0 |
| R005 | `refs/heads/chore/no-fixed-call-cap` | `e2131b1766ec001f7e82b19ade568b563e1230ee` | 351/0 |
| R006 | `refs/heads/chore/no-git-stash` | `2385a4b727fb5ac5c6007c9b93881b3cb696cf95` | 286/0 |
| R007 | `refs/heads/chore/rbmk-hard-floor` | `3bf94ec3e47d57b02a666ee989d866fee656b659` | 285/0 |
| R008 | `refs/heads/docs/design-test-runner` | `815f4b6f03bea9ed029ecd6db9b75e368f5c591d` | 176/0 |
| R009 | `refs/heads/docs/fake-model-plumbing-exception` | `7fe9a0f87558602e7833e4f8d6719a660f1ec041` | 370/0 |
| R010 | `refs/heads/docs/launcher-absorb-breakit` | `fdfeb35a2b262db1fe35640c5c1be9827a8b6772` | 95/0 |
| R011 | `refs/heads/docs/launcher-decisions` | `62e223821ff941c42bb1c0c73b72f85be656af47` | 127/0 |
| R012 | `refs/heads/docs/memory-rules-into-repo` | `c36016bda84c7b410e5783134d4b875339591401` | 217/0 |
| R013 | `refs/heads/docs/onboarding-rule-no-shortcuts` | `ea12b41439e123695f1cfe29f250bce1df3878b2` | 371/0 |
| R014 | `refs/heads/docs/redesign-not-patch` | `e501140e6e59a7cc201db83a172bbf4e33cd45cc` | 283/0 |
| R015 | `refs/heads/docs/test-runner-absorb-breakit` | `e399a1d4a21c26c05064367000fcafb93d4880cd` | 109/0 |
| R016 | `refs/heads/docs/test-runner-no-invented-bounds` | `29748c1149ea91b50fb21512719409675ded7f06` | 149/0 |
| R017 | `refs/heads/docs/test-runner-strip-constants` | `d42676a9f0668f14551a15469b9b24bc14945792` | 130/0 |
| R018 | `refs/heads/docs/three-third-party-notice` | `b48191511cb94b74c4dd6ba10f0294676ed4a9fb` | 332/0 |
| R019 | `refs/heads/docs/two-policy-lines` | `7b89c640f05e64bf030815ff9477315c438c247b` | 178/0 |
| R020 | `refs/heads/feat/board-roster` | `44433d5548781004d02f5623a92e444ef3db117c` | 186/0 |
| R021 | `refs/heads/feat/chat-start-options` | `81355c7eb32d0e94e23237e9a26d6be14c6a7a3a` | 682/0 |
| R022 | `refs/heads/feat/daemon-runs-as-poolhouse` | `e97621879dac95bd7f70a17352a4f0ab8db82923` | 106/0 |
| R023 | `refs/heads/feat/example-rbmk-operator` | `128b7e2c796297868e86b2b6c805855d7e425b95` | 140/0 |
| R024 | `refs/heads/feat/lan-dev-pool-test-sharding` | `863b331fc6fc129277896b00c73f449f58ffda3e` | 260/0 |
| R025 | `refs/heads/feat/phone-free-slice-1` | `529f81be4c8266fae8e2bac60f24cfd1ea7977f6` | 1141/7 |
| R026 | `refs/heads/feat/rbmk-control-room-2d` | `21978bfd6191ca446810adc4a7a3c5a55e15414d` | 279/0 |
| R027 | `refs/heads/feat/rbmk-one-app` | `e5e4ccfe79c5c7ca12d721f3750189b5d78b4375` | 79/0 |
| R028 | `refs/heads/feat/rbmk-operator-web` | `cec3fad671bb8a508b8e8684a73808a91a70bacb` | 326/0 |
| R029 | `refs/heads/feat/sandbox-exclusions` | `82f49f4c700437b0095d96f1dc26880486d0462c` | 627/0 |
| R030 | `refs/heads/feat/studio-adapters` | `55d2e8dd6860268af85bf301d38f59d9b47aa8a7` | 1247/9 |
| R031 | `refs/heads/feat/studio-compute-artifact` | `aafd5fbe55e7c36e87b841786d2604dd7e12f101` | 1205/13 |
| R032 | `refs/heads/feat/studio-decision-configure` | `eaf62a62ed4213871f7794759b0f00c49c10c0ad` | 1055/4 |
| R033 | `refs/heads/feat/studio-decision-design` | `3436a6db9b53eef460623084c29184ff510af521` | 1055/2 |
| R034 | `refs/heads/feat/studio-engine` | `fe1ed0be982079301831b01c7ccfbd0ed32371a9` | 1247/8 |
| R035 | `refs/heads/feat/studio-stream-delivery` | `49dc768e2103721122a83f53c088dec760c505d1` | 1055/6 |
| R036 | `refs/heads/feat/test-runner-steps-0-2` | `a308658101c2adc21615f88fcf17f7fb88ef949e` | 2/0 |
| R037 | `refs/heads/feat/tool-call-milestones` | `b9a3687ad56c46cc566bd5d59a4b6b37ae8737cb` | 343/0 |
| R038 | `refs/heads/feat/unified-chat` | `4abe7c6e54746f978b52c3af4f63cff79677d3d3` | 693/0 |
| R039 | `refs/heads/fix/benchmark-compute-bytecode` | `bbf112c46602cb183fc1d1244606282a66b0119f` | 983/1 |
| R040 | `refs/heads/fix/board-quota-regression` | `41439e6bcf1807885ee56c5856f6190dd3f95137` | 499/0 |
| R041 | `refs/heads/fix/broker-reap-killed-servers` | `b91954a33a51079f9c3662646dd5d32f4fe9ad93` | 174/0 |
| R042 | `refs/heads/fix/broker-startup-crash-loud` | `cd62703f93e2f4d127d5aa639935ad232b9ec252` | 219/0 |
| R043 | `refs/heads/fix/browser-fixture-overhead` | `ecbba5b8f530a72f4bfad7ab7d20310c08c25531` | 476/0 |
| R044 | `refs/heads/fix/chat-conversation-load-controls` | `047b636ef82a8cac496303e039f7142405df02e2` | 692/0 |
| R045 | `refs/heads/fix/chat-inline-model-selection` | `958da174c16781d7b5b88daf64f268ecd4985a5a` | 690/0 |
| R046 | `refs/heads/fix/chat-plain-http-and-walk` | `bf17c71cd93cb47416a69d10a6381e2896afd82a` | 192/0 |
| R047 | `refs/heads/fix/codex-native-session` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | 0/0 |
| R048 | `refs/heads/fix/coding-contract-tests` | `66155fc041a9ea6702fd44822c008f0b008f35d7` | 477/0 |
| R049 | `refs/heads/fix/conversation-journeys` | `a605846fac14f0a21b50efb7e10a4ca19d7cdaa9` | 497/0 |
| R050 | `refs/heads/fix/decider-one-token-limit` | `36d6f3038f460254732dcc139ee19e265791da0c` | 180/0 |
| R051 | `refs/heads/fix/gym-explicit-controller` | `d6d6c425acf6bee63fc82d447109de7da1f14927` | 678/1 |
| R052 | `refs/heads/fix/idempotent-claim-release` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | 0/0 |
| R053 | `refs/heads/fix/native-test-cold-build-timeout` | `6780925157c65f98b1d1980e006cfca5365fa05e` | 207/0 |
| R054 | `refs/heads/fix/no-arbitrary-limits` | `41d07e10f9a6b8b75d978614a9a03c2ef0bcb289` | 12/148 |
| R055 | `refs/heads/fix/no-arbitrary-limits-bench` | `fb265362f256ef57bc4d033f27c5adbd41caa782` | 64/125 |
| R056 | `refs/heads/fix/no-arbitrary-limits-board` | `4582d801bbbbcd09d74db5b830dc2f09dd61ec8a` | 147/7 |
| R057 | `refs/heads/fix/no-arbitrary-limits-board-security` | `f4108781eccedf7c44fc11c1f16f174083e20558` | 84/79 |
| R058 | `refs/heads/fix/no-arbitrary-limits-fleet` | `838261dea3048754466f8aa4777bdeb39074f066` | 147/11 |
| R059 | `refs/heads/fix/no-arbitrary-limits-fleet-security` | `e8a40a53271403dd554fa239694c804f6a9db689` | 64/126 |
| R060 | `refs/heads/fix/no-arbitrary-limits-guard` | `40f290381f2b3f159ea44d8c510c92551c5fa209` | 64/140 |
| R061 | `refs/heads/fix/no-arbitrary-limits-guard-security` | `83ae4c5b6d5526811b62b3f6978603f658ffa600` | 147/6 |
| R062 | `refs/heads/fix/no-arbitrary-limits-net` | `5ce43a17650b99df99f7afc80a315374fd6b2974` | 64/125 |
| R063 | `refs/heads/fix/no-arbitrary-limits-net-security` | `57df071e03984218dd942e0aee6940690bd394e3` | 64/126 |
| R064 | `refs/heads/fix/no-arbitrary-limits-rust` | `ae159f8ff2331b308499349983bab7bcd4aee1d7` | 64/13 |
| R065 | `refs/heads/fix/no-arbitrary-limits-serve` | `958941540f7dca847f684b2a5835208efdd8a167` | 138/9 |
| R066 | `refs/heads/fix/no-arbitrary-limits-serve-security` | `a215dd7f0f8061c9f4a1be76a76e39008783a9cc` | 31/146 |
| R067 | `refs/heads/fix/no-arbitrary-limits-toplevel` | `098b07a88fe7ec644600314b5d920aae79c27766` | 64/104 |
| R068 | `refs/heads/fix/no-arbitrary-limits-toplevel-security` | `bc1b7608f009bde06f783229f8cb598406015d8d` | 64/124 |
| R069 | `refs/heads/fix/no-arbitrary-limits-workspace` | `b2bd876daae0fd25490b86676809be80f5b408bc` | 147/7 |
| R070 | `refs/heads/fix/no-arbitrary-limits-workspace-security` | `ec2a13766899f32128483b1b67f0ee4ff6d14d4a` | 64/125 |
| R071 | `refs/heads/fix/no-fake-models-redteam-rest` | `e1cb839f1a8543b525514bcfc6785aaacac2ce29` | 223/0 |
| R072 | `refs/heads/fix/no-fake-models-shipped` | `5b2c6f2becf049603679ab656a3cbef84ccd1178` | 333/0 |
| R073 | `refs/heads/fix/no-macos-network-prompts` | `291a7a7a979e71847258ec5bd34366f583a7b183` | 182/0 |
| R074 | `refs/heads/fix/owned-broker-bytecode` | `ec476c1ce4e8c68c914da756262f98c4ca554ccf` | 935/1 |
| R075 | `refs/heads/fix/poolhouse-ui-namespace` | `10181aafb52759eade56b4cd3e77ec9e0e5a05fa` | 505/0 |
| R076 | `refs/heads/fix/process-scan-psutil-systemerror` | `5be4e30e77978da87061d9a70dd99be7c1248510` | 288/0 |
| R077 | `refs/heads/fix/pyright-budget-slow` | `ab63ced7b5e630e671e4cd076e81445ef4af5dc4` | 204/0 |
| R078 | `refs/heads/fix/red-glossary-briefing` | `19dfd9b64ec9661e7a0b5ca631ffbfd94bc29e8e` | 337/0 |
| R079 | `refs/heads/fix/red-layers-cli-testfarm` | `1fa732fb01788a7488fd0a6e751769b8c793bc9c` | 196/0 |
| R080 | `refs/heads/fix/red-on-development-tip` | `be5020d918ba6380b016dd6b02e6b33a97bf6ff4` | 31/4 |
| R081 | `refs/heads/fix/restart-host-and-ensure-honesty` | `6ffbb592ba1cb9b09119263c9e3f78bff60fb9d5` | 16/0 |
| R082 | `refs/heads/fix/sandbox-holes-and-harness-denial` | `f38cdce1953d944172b7e38e99c7a2b69eb7378a` | 128/10 |
| R083 | `refs/heads/fix/sandbox-l1` | `a86c509ed75c13971af1898cf5e1781edfd8818f` | 36/6 |
| R084 | `refs/heads/fix/serve-family-red-tests` | `143675439cade20cb43fec33e9c147ef7ca69b32` | 302/0 |
| R085 | `refs/heads/fix/serving-defaults-real-fit` | `f19f7026a304472349108345374a86345cd8bb18` | 45/0 |
| R086 | `refs/heads/fix/studio-cache-digest` | `0d6475815e6f4cb7d6ec4385a084873099d5b468` | 1114/1 |
| R087 | `refs/heads/fix/studio-candidate-builder` | `cc89773ee07dbb6fca0b6a5ae46ce0f4ba54ea2c` | 983/2 |
| R088 | `refs/heads/fix/studio-compute-update` | `00a88a3d44fdadcefe4437ee550c194455e35ad7` | 988/0 |
| R089 | `refs/heads/fix/studio-fixed-decision-staging` | `ec158ffa4789135349a7e1e470029ba4e9355f20` | 1055/3 |
| R090 | `refs/heads/fix/studio-mlx-serving-dependencies` | `2d7b6abfb7d54e7f4e24ced590320bbd62f28aeb` | 1012/1 |
| R091 | `refs/heads/fix/studio-python-launch` | `57fe1532fbad578bf4a14af84970d66e8e6355b9` | 1032/1 |
| R092 | `refs/heads/fix/switch-rows-heavy-modules` | `764f2df7cbe864cac97430205dfce8fc35510120` | 309/0 |
| R093 | `refs/heads/fix/testslots-status-flake` | `8310eaa0640c1417074b275021473ad2352d05d4` | 300/0 |
| R094 | `refs/heads/fix/ui-design-fixture-journeys` | `3feecbe293ef8f3bd7d5fb43ab20fd28d96e81fb` | 498/0 |
| R095 | `refs/heads/land/linux` | `11faf8036a31ff50474b6e472aab7da217e906b5` | 47/0 |
| R096 | `refs/heads/main` | `cf364a1fa432198435071789acb9ffdd2158fb0a` | 5755/0 |
| R097 | `refs/heads/review/board-limits` | `3f85d82063d338e6903d607f7dbf943a322ee05c` | 147/14 |
| R098 | `refs/heads/review/fleet-limits` | `48ca650d540d78a965c32751de33d3bf17da7142` | 147/20 |
| R099 | `refs/heads/review/net-security-limits` | `b429acd9c3a25fafb655783b119409d0ee6cb7bf` | 147/13 |
| R100 | `refs/heads/review/rust-limits` | `75318562353d9bfdd2468dfac6d7071ae6e925a2` | 147/7 |
| R101 | `refs/heads/review/rust-limits-2` | `e7c30ff70bc1eca798ee3a321ebd7383d37bb857` | 64/14 |
| R102 | `refs/heads/review/serve-limits` | `2fa3af96592321ef9d2d6c34747be57063e6e5b4` | 138/10 |
| R103 | `refs/heads/review/toplevel-security` | `00766fa9d10aa197dcd15750a21370d06bcacd8d` | 147/13 |
| R104 | `refs/heads/review/workspace-security` | `450cd887b7960c3c600293a04485561372d8cdb1` | 147/11 |
| R105 | `refs/heads/test/canned-batch-3-serve` | `f7c06d4cf271a2c7fd2241558395e1ecf7d787e4` | 293/0 |
| R106 | `refs/heads/test/canned-batch-4-client` | `5a632cf0a667f4cc3826f716ad4d75c354d2770f` | 270/0 |
| R107 | `refs/heads/test/canned-batch-5-agent` | `7c8743031360f21174a52530087d38c14278e3ef` | 236/0 |
| R108 | `refs/heads/test/canned-batch-6-decide` | `7c8743031360f21174a52530087d38c14278e3ef` | 236/0 |
| R109 | `refs/heads/test/canned-batch-7-graph-chat` | `7c8743031360f21174a52530087d38c14278e3ef` | 236/0 |
| R110 | `refs/heads/test/canned-server` | `5755232256430427243b1e04056e21809cb69840` | 317/0 |
| R111 | `refs/heads/test/real-model-harness` | `e19e69a1f465e42fc84f3efb5814d10cbf26227a` | 322/0 |
| R112 | `refs/heads/worktree-agent-a0807cc3cfdb68792` | `143675439cade20cb43fec33e9c147ef7ca69b32` | 302/0 |
| R113 | `refs/heads/worktree-agent-a182299970f1505db` | `4a3836066e148700fbe7fa8f720584d26966f4f7` | 172/0 |
| R114 | `refs/heads/worktree-agent-a18ae0a52446c1fa2` | `c890587765b1ca5ff96519f00906da9ce3b91e80` | 128/0 |
| R115 | `refs/heads/worktree-agent-a1a4ff19a63605ab9` | `69e77941d6822b7835b0f334c4b41b2cd5a4c3c0` | 147/6 |
| R116 | `refs/heads/worktree-agent-a1fbdf4c7624fe30c` | `2385a4b727fb5ac5c6007c9b93881b3cb696cf95` | 286/0 |
| R117 | `refs/heads/worktree-agent-a1fea269a009ef687` | `ea12b41439e123695f1cfe29f250bce1df3878b2` | 371/0 |
| R118 | `refs/heads/worktree-agent-a203d5d8d24496b3b` | `54bfd115351d75666c5d47da91db98a17f3cf30f` | 257/0 |
| R119 | `refs/heads/worktree-agent-a218e827bb253fb0a` | `bf17c71cd93cb47416a69d10a6381e2896afd82a` | 192/0 |
| R120 | `refs/heads/worktree-agent-a25dd213ba3f1b1d6` | `4df9fedef4bdb07e8aaa7c808fc898459bd485f8` | 1069/0 |
| R121 | `refs/heads/worktree-agent-a2788a150d2cac1b2` | `36d6f3038f460254732dcc139ee19e265791da0c` | 180/0 |
| R122 | `refs/heads/worktree-agent-a27a6834a3a7111f3` | `7c8743031360f21174a52530087d38c14278e3ef` | 236/0 |
| R123 | `refs/heads/worktree-agent-a2b3a156ebb1260ed` | `69e77941d6822b7835b0f334c4b41b2cd5a4c3c0` | 147/6 |
| R124 | `refs/heads/worktree-agent-a2f09e409817d40f1` | `14b603a18d9559c72002ca93fe942f80008792a5` | 147/3 |
| R125 | `refs/heads/worktree-agent-a3183ebf734b5941f` | `fab6db3752d05a13f7a60b6a5666c1338d3e7360` | 180/1 |
| R126 | `refs/heads/worktree-agent-a35395b261e2a2887` | `724881eeb4bf3aafcc4853f6828563f288ff6539` | 97/0 |
| R127 | `refs/heads/worktree-agent-a355ac80c0b9f9463` | `7c8743031360f21174a52530087d38c14278e3ef` | 236/0 |
| R128 | `refs/heads/worktree-agent-a35f5e8b0b356d73a` | `19dfd9b64ec9661e7a0b5ca631ffbfd94bc29e8e` | 337/0 |
| R129 | `refs/heads/worktree-agent-a40775dffa86f9bdd` | `b9a3687ad56c46cc566bd5d59a4b6b37ae8737cb` | 343/0 |
| R130 | `refs/heads/worktree-agent-a4599654f6063491a` | `419537c2c6e38046985a8a4f1cbad7e52a130fe6` | 349/0 |
| R131 | `refs/heads/worktree-agent-a4d5a0672ed948d5a` | `7c8743031360f21174a52530087d38c14278e3ef` | 236/0 |
| R132 | `refs/heads/worktree-agent-a534b7b0aff6abd95` | `6780925157c65f98b1d1980e006cfca5365fa05e` | 207/0 |
| R133 | `refs/heads/worktree-agent-a54688b962ef258e6` | `6f304962b2732afa8e40236c476e5c927e7cac32` | 249/0 |
| R134 | `refs/heads/worktree-agent-a5b5c0d12df73c9be` | `bf17c71cd93cb47416a69d10a6381e2896afd82a` | 192/0 |
| R135 | `refs/heads/worktree-agent-a5c9d53004763f175` | `5755232256430427243b1e04056e21809cb69840` | 317/0 |
| R136 | `refs/heads/worktree-agent-a6c6170b92733a181` | `d42676a9f0668f14551a15469b9b24bc14945792` | 130/0 |
| R137 | `refs/heads/worktree-agent-a70cbd85b3ac372d9` | `229c969dae9b0725733a1350539de8dacfb06237` | 75/0 |
| R138 | `refs/heads/worktree-agent-a715c094d9cb75f02` | `143675439cade20cb43fec33e9c147ef7ca69b32` | 302/0 |
| R139 | `refs/heads/worktree-agent-a73b5688e5ebcd5a0` | `3ae386caa96717dc2009a0a338ce3c3072496e86` | 360/0 |
| R140 | `refs/heads/worktree-agent-a7674c3136345a527` | `fab6db3752d05a13f7a60b6a5666c1338d3e7360` | 180/1 |
| R141 | `refs/heads/worktree-agent-a76ff81511438e108` | `ea12b41439e123695f1cfe29f250bce1df3878b2` | 371/0 |
| R142 | `refs/heads/worktree-agent-a81187afe6abeb7b2` | `5be4e30e77978da87061d9a70dd99be7c1248510` | 288/0 |
| R143 | `refs/heads/worktree-agent-a83d10f8e758ca40b` | `5755232256430427243b1e04056e21809cb69840` | 317/0 |
| R144 | `refs/heads/worktree-agent-a897a7ad0f628f1d4` | `5b2c6f2becf049603679ab656a3cbef84ccd1178` | 333/0 |
| R145 | `refs/heads/worktree-agent-a8b255f4d5c3546b1` | `764f2df7cbe864cac97430205dfce8fc35510120` | 309/0 |
| R146 | `refs/heads/worktree-agent-a8b727e546e8c5375` | `5755232256430427243b1e04056e21809cb69840` | 317/0 |
| R147 | `refs/heads/worktree-agent-a91b5a5b3db2d7211` | `73272f6c4264a87e6a3356f11d9caba695d32ab8` | 1048/0 |
| R148 | `refs/heads/worktree-agent-a92377c1e3e8d3318` | `2c9a52ea2b19db57792f5996b251242c23f7742d` | 290/0 |
| R149 | `refs/heads/worktree-agent-a9295ccab1fb372a8` | `7fe9a0f87558602e7833e4f8d6719a660f1ec041` | 370/0 |
| R150 | `refs/heads/worktree-agent-a9597030a0397ce12` | `291a7a7a979e71847258ec5bd34366f583a7b183` | 182/0 |
| R151 | `refs/heads/worktree-agent-a9e4714b6dad1e714` | `7a1d2f6bcc644866de0bd514c90ac896afed5bd4` | 1100/0 |
| R152 | `refs/heads/worktree-agent-aa1fc5bd7b924d738` | `fab6db3752d05a13f7a60b6a5666c1338d3e7360` | 180/1 |
| R153 | `refs/heads/worktree-agent-aa4d634b049d69d19` | `01b9eef9a4873b168e35681ae637573277e5ac74` | 306/0 |
| R154 | `refs/heads/worktree-agent-aa70e2df41234a1db` | `e2131b1766ec001f7e82b19ade568b563e1230ee` | 351/0 |
| R155 | `refs/heads/worktree-agent-aa7afe968ba9f8a89` | `fab6db3752d05a13f7a60b6a5666c1338d3e7360` | 180/1 |
| R156 | `refs/heads/worktree-agent-aaa62af39f5aaa9d6` | `94f16aa187dcad74346d64a5d32102edbd980883` | 372/0 |
| R157 | `refs/heads/worktree-agent-aac5b79bd43524675` | `5755232256430427243b1e04056e21809cb69840` | 317/0 |
| R158 | `refs/heads/worktree-agent-ab8a61858fc9f34b2` | `e2131b1766ec001f7e82b19ade568b563e1230ee` | 351/0 |
| R159 | `refs/heads/worktree-agent-abd9ed7150170e0d2` | `fc85a8f15afb5ceead422cee5b93eb44f42d9df2` | 313/0 |
| R160 | `refs/heads/worktree-agent-ac071ab204615561d` | `5755232256430427243b1e04056e21809cb69840` | 317/0 |
| R161 | `refs/heads/worktree-agent-ac55104966fa2148a` | `2c9a52ea2b19db57792f5996b251242c23f7742d` | 290/0 |
| R162 | `refs/heads/worktree-agent-ac77732d307031711` | `863b331fc6fc129277896b00c73f449f58ffda3e` | 260/0 |
| R163 | `refs/heads/worktree-agent-acd8343b1ad605217` | `863b331fc6fc129277896b00c73f449f58ffda3e` | 260/0 |
| R164 | `refs/heads/worktree-agent-ad2c517480c4b4ee4` | `ae159f8ff2331b308499349983bab7bcd4aee1d7` | 64/13 |
| R165 | `refs/heads/worktree-agent-ad62b03c89068d81f` | `f7c06d4cf271a2c7fd2241558395e1ecf7d787e4` | 293/0 |
| R166 | `refs/heads/worktree-agent-ad76a3bf49ac51fc1` | `5adcaea4a229238a280acac6276f6bd239c4478d` | 358/0 |
| R167 | `refs/heads/worktree-agent-adc5d7f61730dd23b` | `540bbc0efe0552efa741ee19d1af5b59ad9b35e9` | 31/0 |
| R168 | `refs/heads/worktree-agent-ae0075ec3e28bd382` | `529b957f38a46b8a486f780bf8206ff0e40378d2` | 152/0 |
| R169 | `refs/heads/worktree-agent-ae04626e1dec6608c` | `5be4e30e77978da87061d9a70dd99be7c1248510` | 288/0 |
| R170 | `refs/heads/worktree-agent-ae883f3a50f0f3cd0` | `fab6db3752d05a13f7a60b6a5666c1338d3e7360` | 180/1 |
| R171 | `refs/heads/worktree-agent-aeaf0c0e05a2c3480` | `1f7e140ae16307f441c6edcbdc051afcc0951daf` | 113/0 |
| R172 | `refs/heads/worktree-agent-aec2b5c3d8c478a8f` | `540bbc0efe0552efa741ee19d1af5b59ad9b35e9` | 31/0 |
| R173 | `refs/heads/worktree-agent-af390cb64879c321b` | `6780925157c65f98b1d1980e006cfca5365fa05e` | 207/0 |
| R174 | `refs/heads/worktree-agent-af4afa033bced0241` | `94f16aa187dcad74346d64a5d32102edbd980883` | 372/0 |
| R175 | `refs/heads/worktree-agent-af676e68685ee0af3` | `5adcaea4a229238a280acac6276f6bd239c4478d` | 358/0 |
| R176 | `refs/heads/worktree-agent-afc42ff7fb2293126` | `e32433541ad28070d6a8bd32ff441a5581c71cab` | 161/0 |
| R177 | `refs/heads/worktree-agent-aff3ff9be64e5c55c` | `7c8743031360f21174a52530087d38c14278e3ef` | 236/0 |
| R178 | `refs/heads/worktree-agent-aff8a646ec0569a2c` | `5adcaea4a229238a280acac6276f6bd239c4478d` | 358/0 |
| R179 | `refs/remotes/origin/0.2dev` | `43ede16661ef7acfe9c64a02140a5b73f661f0b7` | 2594/0 |
| R180 | `refs/remotes/origin/0.3dev` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | 0/0 |
| R181 | `refs/remotes/origin/HEAD` | `da68aa99d084170c408e49f473074dd210dd1e6d` | 5754/0 |
| R182 | `refs/remotes/origin/feat/decider-gemma4` | `8120e9a11cf73b665b322236af49a86bb70d6f90` | 5353/13 |
| R183 | `refs/remotes/origin/main` | `da68aa99d084170c408e49f473074dd210dd1e6d` | 5754/0 |
| R184 | `refs/remotes/origin/promote/2026-10-09` | `c646024b1e95ed3c47bf74cd04154b3b361ad9fa` | 2650/1 |
| R185 | `refs/remotes/origin/promote/2026-10-09b` | `d432d9c260615fb5fb89eb799e02a2e15e3ed549` | 2648/2 |
| R186 | `refs/remotes/origin/promote/2026-10-09c` | `ef808445ec7af547d618986f9f538ffc6de19620` | 2641/1 |
| R187 | `refs/remotes/origin/promote/2026-10-09d` | `e1212b12b4d70b9e067dac08d8b80be12d00e3ee` | 2641/54 |
| R188 | `refs/remotes/origin/promote/2026-10-09e` | `0c96cb4f2bf61e6ef16dc9dae92f3e21009b0a52` | 2641/56 |
| R189 | `refs/remotes/origin/promote/2026-10-09f` | `b95b1c9c8c05d5f44c0402f1fdf9144ef4689253` | 2641/60 |
| R190 | `refs/remotes/origin/promote/2026-10-09g` | `ff3a46c00326066cf70d1482b72fdcf779122f9d` | 2641/62 |
| R191 | `refs/remotes/origin/release-please--branches--main--components--ml-stack` | `08590c056e0d85891bab73f03c36d229743a1a50` | 5755/1 |

## Existing external bundle manifest

All 14 entries were read-only hashed, `git bundle verify` exit 0, and `git bundle list-heads` exit 0 at the original checkpoint. Every recorded verifier output explicitly says **complete history**: no incremental prerequisites were reported. Bundle timestamps and sizes belong to that checkpoint. This is the inventory worker’s verification of retained files, not a new independent import or verification on the other device. A second independent empty-repository/other-device verification and physical transfer remain unrecorded.

### B01: studio-contracts-20261019.bundle

Custody: `H-MAC/studio-contracts-20261019.bundle`; 22258591 bytes; SHA-256 `d91a856be3c4b51de65140289667fdc2b08dbd80b66f14c963beeb2d59a9f442`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/feat/studio-contracts` | `e5680462b88c57cf1f0419454ec1355ab38baed4` |
| `refs/heads/feat/studio-model-types` | `48846a8f8118c9dc4504618a37032a7aa851feb0` |

### B02: studio-webhook-ingress-20261019.bundle

Custody: `H-MAC/studio-webhook-ingress-20261019.bundle`; 23013053 bytes; SHA-256 `bca6db8686f001c013658c23cbda9745650151b22af769371a6d7932da5f09fd`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/feat/studio-webhook-ingress` | `a7bea338955ceb7bd27b0fa934cf66c413fb1145` |

### B03: studio-saved-models-20261019.bundle

Custody: `H-MAC/studio-saved-models-20261019.bundle`; 22718047 bytes; SHA-256 `c9b43c5284da408ec17060b9838c5f70a64016ba9f2244a55c3a70a04994ab1b`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/feat/studio-saved-models` | `9e372b9df65dd521ee5657695489e3b9a8f72e17` |

### B04: studio-worker-calibration-20261019.bundle

Custody: `H-MAC/studio-worker-calibration-20261019.bundle`; 23084441 bytes; SHA-256 `431a79346fd1cdfa4331ffccece7e85ed5e5f543679ad4f5445d6fe9a5e05f01`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/fix/studio-worker-calibration` | `653d31cd7ad35b3644d71d04fe6a0bae68e2c2f8` |

### B05: studio-native-render-20261019.bundle

Custody: `H-MAC/studio-native-render-20261019.bundle`; 23103926 bytes; SHA-256 `b269034845c7a90d12cd3d568b9839742267188db4e6a623496b931fd33cc7ff`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/fix/studio-native-render` | `f3b038e41b6e7e556bfdf65bdcebe7591cdad4e5` |

### B06: studio-worker-proof-20261019.bundle

Custody: `H-MAC/studio-worker-proof-20261019.bundle`; 22913494 bytes; SHA-256 `e4450054f549fed143da0175f4a2c74f398b37cf082cbf7134beb58c26dafbfd`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/feat/studio-worker-proof` | `3b68c0502aaba0cd9dcf6c09685e6a3e0230f024` |

### B07: studio-checkpoint-assets-20261019.bundle

Custody: `H-MAC/studio-checkpoint-assets-20261019.bundle`; 22307549 bytes; SHA-256 `89f1c9bad83a263e71b776942440d242643c0eff063cdaf4916dc6639c914d50`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/feat/studio-checkpoint-assets` | `07afbd4582e355b0443054efaf93bd07a6db6631` |

### B08: studio-managed-browser-20261020.bundle

Custody: `H-MAC/studio-managed-browser-20261020.bundle`; 23281754 bytes; SHA-256 `0c2b506030c24b449ab8b4c0679d72e7c3cf2b2ba2d40d6bb56890f743088295`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/fix/studio-managed-browser` | `b286eca895f7cb5b25b03c1896391d98fea10e19` |

### B09: studio-native-watch-20261019.bundle

Custody: `H-MAC/studio-native-watch-20261019.bundle`; 22811522 bytes; SHA-256 `1c89bd81b871c0646f3dd154c83b18a28daca5c7e7882b16c67a134a268c3bd1`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/feat/studio-native-watch-design` | `4b4d70a48a2f434e0b154b257fca12d6e0ca225b` |

### B10: studio-producer-integration-695e3a8c6.bundle

Custody: `H-MAC/studio-producer-integration-695e3a8c6.bundle`; 23278806 bytes; SHA-256 `96abdc13d7f5c8711ea84387b3e10aed95694fe2cd7ad3321c120043a7ffc1f4`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/feat/studio-producer-integrity-integration` | `695e3a8c6dc90e8812d9169ba0129d1528fb4985` |

### B11: studio-transfer-20261019.bundle

Custody: `H-MAC/studio-transfer-20261019.bundle`; 22277222 bytes; SHA-256 `e680afd1ff6ae9741f16ffb21d98174ae27590ffa840f9d79efb0875cc8b24b3`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/feat/studio-transfer` | `20888461ab90b4e602cd25799f18074fb757a246` |

### B12: studio-saved-pins-20261019.bundle

Custody: `H-MAC/studio-saved-pins-20261019.bundle`; 22912060 bytes; SHA-256 `376f3f99acfe330d44df87253ce02b80621e698cc18fed4e68e173b9b3d0bb81`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/feat/studio-saved-pins` | `89a6ead094f69e8f1197d03b1d232c0e63bde20e` |

### B13: studio-native-build-20261020.bundle

Custody: `H-MAC/studio-native-build-20261020.bundle`; 23254307 bytes; SHA-256 `98e1c25f865ca1d5f3bf2725831fba97afca8e642310b257fb99d5cf44a2d898`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/build/studio-native-ba7` | `1b47703b54a02ac132f129443660102fc4e33a4b` |

### B14: studio-watch-replacement-20261020.bundle

Custody: `H-MAC/studio-watch-replacement-20261020.bundle`; 23189644 bytes; SHA-256 `fb635c1924c3dc250815c74070d9b2eb7f0344bb44a0d193ea44c6840cfd8a34`. Complete history; verify/list-heads exit 0; incremental prerequisites: none reported.

| Advertised ref | Full advertised head |
| --- | --- |
| `refs/heads/fix/studio-watch-replacement` | `97d4ee50652e8a842e8ad9db57670cf029d6dbd5` |

## Dirty and untracked custody

Seven original trees contain 39 nonempty porcelain records. These are metadata-only identities below; no patch, index image, untracked-file content archive or independent apply-check was made by this inventory. The original working directories/indexes are their byte custody. Rows must not be represented as externally backed up, merged, disposable or currently unchanged. Index and worktree status letters are retained separately.

| Tree | Porcelain XY | Relative path | Custody / gap |
| --- | --- | --- | --- |
| M03 | `??` | `tests/test_guard_unbounded_reading.py` | original index/worktree; external byte preservation unproven |
| M06 | `??` | `tests/test_claim_release.py` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/board.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/board/rotate.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/files.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/gov/describe.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/gov/mod.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/gov/ops.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/gov/room.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/logbounds.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/row.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/scratch.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/sessions.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/src/shard/host.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/tests/files.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/tests/gov_act_size.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/tests/gov_floods.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/tests/log_bounds.rs` | original index/worktree; external byte preservation unproven |
| M17 | `A ` | `app/poolhouse-node/tests/review_rust_acts_memory.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/tests/review_rust_limits.rs` | original index/worktree; external byte preservation unproven |
| M17 | `A ` | `app/poolhouse-node/tests/review_rust_limits2.rs` | original index/worktree; external byte preservation unproven |
| M17 | `A ` | `app/poolhouse-node/tests/review_rust_memory2.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `app/poolhouse-node/tests/scratch.rs` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `docs/limits.md` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `docs/node.md` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `docs/notes/attack-no-arbitrary-limits-rust.md` | original index/worktree; external byte preservation unproven |
| M17 | ` M` | `docs/pools-and-serving.md` | original index/worktree; external byte preservation unproven |
| M31 | ` M` | `src/poolhouse/bench/serve.py` | original index/worktree; external byte preservation unproven |
| M31 | ` M` | `src/poolhouse/client/health.py` | original index/worktree; external byte preservation unproven |
| M31 | ` M` | `src/poolhouse/serve/admitting.py` | original index/worktree; external byte preservation unproven |
| M31 | ` M` | `src/poolhouse/serve/backend.py` | original index/worktree; external byte preservation unproven |
| M31 | ` M` | `src/poolhouse/serve/manager.py` | original index/worktree; external byte preservation unproven |
| M31 | ` M` | `src/poolhouse/serve/mlx_tree.py` | original index/worktree; external byte preservation unproven |
| M31 | ` M` | `src/poolhouse/serve/python_engines.py` | original index/worktree; external byte preservation unproven |
| M31 | `??` | `src/poolhouse/serve/loadguard.py` | original index/worktree; external byte preservation unproven |
| M36 | ` M` | `src/poolhouse/client/spent.py` | original index/worktree; external byte preservation unproven |
| M53 | ` M` | `docs/redteam/coverage-map.toml` | original index/worktree; external byte preservation unproven |
| M53 | ` M` | `docs/redteam/coverage.json` | original index/worktree; external byte preservation unproven |
| M54 | `??` | `examples/rbmk_experiment/decider/upstream_worker.py` | original index/worktree; external byte preservation unproven |

M06/M07 were active fix checkouts at inventory time. Later reviewed release/selector commits are separate checkpoints; the existence of later commits does not prove every original dirty/untracked byte was retained. No original dirty source is removed by this ledger.

## Ignored assets requiring disposition

The ignored-path counts in the worktree table are recursive Git ignored path entries, not top-level entries or asset counts. `ignored-assets.json` separately records 320 metadata candidates in seven trees after a heuristic cache/sensitive-store exclusion. Some candidates are generated caches or artifacts; the heuristic is not a reproducibility proof. No candidate content was copied for this ledger. Original custody and behavior/rebuildability review remain required; no cleanup follows from a cache-like name.

| Tree | Recorded candidate groups | Count | Disposition |
| --- | --- | --- | --- |
| M01 | `.claude`: 45; `.full-tier-last.json`: 1; `app`: 250; `tests`: 3 | 299 | retained locally; classification/copy/rebuildability pending |
| M12 | `examples`: 1 | 1 | retained locally; classification/copy/rebuildability pending |
| M16 | `.testmondata`: 1; `.testmondata-shm`: 1; `.testmondata-wal`: 1 | 3 | retained locally; classification/copy/rebuildability pending |
| M17 | `app`: 10 | 10 | retained locally; classification/copy/rebuildability pending |
| M23 | `tests`: 3 | 3 | retained locally; classification/copy/rebuildability pending |
| M40 | `examples`: 1 | 1 | retained locally; classification/copy/rebuildability pending |
| M51 | `tests`: 3 | 3 | retained locally; classification/copy/rebuildability pending |

Two non-rebuildability candidates have recorded content digests; each original remains the only custody established by this evidence:

| Tree | Relative asset | Bytes | Recorded SHA-256 |
| --- | --- | --- | --- |
| M12 | `examples/rbmk/room/room.glb` | 1642164 | `b2cd3e397b7fff3a89b9e9522060e5c6c0184f61fde05cf9c50f5477fcf02826` |
| M40 | `examples/rbmk_control_room/viewer/room.glb` | 1661732 | `20394f344015fc677560695b32d76c43d7d679cc7ec16701bbee4cb94c9acd46` |

Other specific metadata: M01 local settings (2533 bytes), scheduling lock (130), `.full-tier-last.json` (87), 250 `app/` candidates and three vendor files; M16 `.testmondata` (17088512), SHM (65536) and WAL (3543232); M17 ten generated binary/schema/permission entries including a zero-byte native binary placeholder; M23/M51 each retain three vendor files (1313897, 279706, 20968 bytes). Nested worktree directories in M01 are locators, not independent backups. Local settings and runtime state contents were neither read nor copied; no runtime state may be replayed as authority. Other model caches/datasets and excluded sensitive stores were not inventoried by this evidence.

## Paused remote model and behavior ownership

Paused work is retained exactly as `refs/remotes/origin/feat/decider-gemma4` = `8120e9a11cf73b665b322236af49a86bb70d6f90`, with **13 unique commits** against the original baseline. No model work was resumed. No listed bundle advertises this ref/head; inclusion in any bundle’s internal object history was not established. A separate offline copy, remote object availability now, required behavior mapping and resumption owner remain unverified. This is not permission to fetch a model, start a server or migrate trust.

| Source group | Known retained evidence | Outstanding owner / behavior mapping |
| --- | --- | --- |
| M02/M04/M05 and other U>0 rows | Full heads, original ref comparison and unique-commit counts retained | Coordinating agent must assign source owners; native limits, security and cleanup behavior requires code/test mapping. No wholesale supersession. |
| M12/M40 visual assets | Original Git heads and two GLB digests above | Asset custodian/replacement mapping unassigned; establish another verified copy before cleanup. |
| M37 native-service work | Original head e97621879dac95bd7f70a17352a4f0ab8db82923; baseline installed process used ordinary UID, not dedicated service identity | Native lifecycle/service owner must prove actual replacement behavior; historical branch title is not acceptance. |
| B01–B14 studio/native/worker branches | Complete-history bundles and advertised heads retained | Per-bundle behavior owner and mapping to the common native plan remain unassigned. Bundle validity does not prove native replacements. |
| Paused remote model | Exact ref/head and 13 unique commits retained | Resumption, independent preservation and native behavior disposition require coordination; remains paused. |
| Cross-device source requests | This Mac ledger and the linked other-device ledger are real Git handoff data | No authenticated device/API connection or pool adoption is established; unresolved source custody/behavior gaps remain part of K. |

## Later scoped closures, separate from originals

These receipts concern task-created review/worker checkouts, not a global cleanup of M01–M54. No review-only code or original dirty work was discarded. The live native host was not replaced by the producer experiments.

| Scoped checkout/ref | Landed/reviewed full commit | Recorded closure and limit |
| --- | --- | --- |
| review/idempotent-claim-release | 5c3a87546efb992acc8f7ec168ea5f4c76eca4c4 | Review tree removed after exact restoration; no review-only edits. Later review-ref removal was coordinated separately, not asserted by the original receipt. Does not close original M06 custody gaps. |
| review/codex-native-session | 6edfb392d61f167cf42ac7e889f4176034e358a0 | Review tree removed after clean restoration; no review-only edits. Does not close original M07 custody gaps. |
| feat/macos-producer-candidate | 24c46d8ae687f064fd75dc994e5931ee29a1ac63 | 2026-10-23T22:10:06.035806+00:00 receipt verifies directory, registration and branch removed, all own claims released. Source landed; actual two-package preparation verified by coordinator. Explicitly UNTRUSTED, no H/K/P acceptance. |

E-MAC retains `claim-release-review.json`, `codex-selector-review.json` and `macos-candidate-author/cleanup.json`. Current ref/worktree state outside these scoped receipts is unknown here. Original physical work, external bundles, source checkout and package/probe artifacts remain outside the closed helper checkout. Storage metadata, agent coordination messages and receipts cannot grant authority or establish protected custody.

## Remaining handoff gaps

The coordinator must assign behavior/disposition owners, map unique source to native implementations and real tests, establish byte preservation for original dirty/untracked and non-rebuildable assets, confirm paused-model external custody, and arrange independently verified transfer where needed. Full bundle/ref object availability on the other device and unresolved launch-ticket evidence in the linked ledger remain open. No new archive campaign, broad test suite, credential read, model execution or native acceptance was performed to publish this ledger.
