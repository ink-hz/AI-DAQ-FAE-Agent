# SDK coverage audit — 2026-10-16

The local archive contains 143 original files and 6.83 GB. The active published release has 15 records; the current Dev candidate has 22 records from 8 exact packages. The [file-by-file review](../../docs/reviews/2026-10-16-daq-sdk-material-coverage.md) finds 115 distinct file hashes, 28 duplicate copies, 22 change logs and two unverified shortcut files. The [machine-readable inventory](../material_inventory.jsonl) includes all 143 items as asset metadata with `device_support=unknown`; its presence does not establish an API fact or tested combination.

| Archived source family | Current SDK usage facts | Next source review |
| --- | --- | --- |
| OrbbecSDK 2.9.0 Linux x86_64, 2026-09-29 | EGO host C++ examples, network UID hot-plug candidate, and one build map | Remaining EGO examples, general API, build and troubleshooting; UMI Finger magnetometer note conflicts with retained sample |
| OrbbecSDK 2.9.0 Linux x86_64, 2026-09-21/23 | SD card API and example only | Other build-specific EGO examples and change notices |
| OrbbecSDK 2.9.0 Linux x86_64, 2026-09-10/17 | No answerable records | Source-checked build-specific records where user demand warrants |
| libobkit 2026-09-09 RK3576/AArch64 | Device scope, build, capture lifecycle | Other C APIs, error handling and integration examples |
| OrbbecSDK host ARM64, Windows, macOS and iOS | iPhoneOS framework packaging only | Other platform-specific headers, examples and installation instructions; Swift API and device support remain unreviewed |
| Android Wrapper, EgoLowBle and engineering extensions | Android Java firmware-log, SD-card and state API records; Windows/Linux x64 BLE integration records | Android Wrapper beta2.2.5 and EgoLowBle 1.1.8 are exact package scopes; other dates/platforms/AAR/engineering extensions remain unreviewed |
| EgoViewer packages and firmware/image bundles | No SDK usage or compatibility records | 22 change logs reviewed as versioned release-note candidates, not tested combinations; Android 1.0.11 has several distinct builds |

An unreviewed family is **no data in this catalog**, never a claim that the product or API is unsupported. SDK examples may explain how an API is called; `check_software_support` must still establish exact product, revision, connection, platform, software and firmware applicability. Camera FAE keeps the same separation between `Knowledge_SDK` and its device support matrix.

For each new drop, verify the original archive manifest, diff package members, review additions and removals, update the exact source spans in `curated.json`, regenerate the catalog, run Dev answer replay, and independently inspect the returned answers and trace before activation. An older positive record remains bound to its original package SHA and build date.
