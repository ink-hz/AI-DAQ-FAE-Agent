# SDK coverage audit — 2026-10-15

The local archive contains 143 original files and 6.83 GB. The 15 published candidate records are a reviewed slice of those originals. Presence of a package in the archive does not establish an API fact or a tested device combination.

| Archived source family | Current SDK usage facts | Next source review |
| --- | --- | --- |
| OrbbecSDK 2.9.0 Linux x86_64, 2026-09-29 | EGO host C++ examples and one build map | Remaining EGO examples, general API, build and troubleshooting |
| OrbbecSDK 2.9.0 Linux x86_64, 2026-09-21/23 | SD card API and example only | Other build-specific EGO examples and change notices |
| OrbbecSDK 2.9.0 Linux x86_64, 2026-09-10/17 | No answerable records | Source-checked build-specific records where user demand warrants |
| libobkit 2026-09-09 RK3576/AArch64 | Device scope, build, capture lifecycle | Other C APIs, error handling and integration examples |
| OrbbecSDK host ARM64, Windows, macOS and iOS | No answerable records | Platform-specific headers, examples and installation instructions |
| Android Wrapper, EgoLowBle and engineering extensions | No answerable records | Separate language/API layers and versioned source traces |
| EgoViewer packages and firmware/image bundles | No SDK usage or compatibility records | Governed software support and exact tested image/device pairing |

An unreviewed family is **no data in this catalog**, never a claim that the product or API is unsupported. SDK examples may explain how an API is called; `check_software_support` must still establish exact product, revision, connection, platform, software and firmware applicability. Camera FAE keeps the same separation between `Knowledge_SDK` and its device support matrix.

For each new drop, verify the original archive manifest, diff package members, review additions and removals, update the exact source spans in `curated.json`, regenerate the catalog, run Dev answer replay, and independently inspect the returned answers and trace before activation. An older positive record remains bound to its original package SHA and build date.
