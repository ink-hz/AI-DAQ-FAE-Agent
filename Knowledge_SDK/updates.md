# SDK build changes reviewed on 2026-10-15

`update_audit.json` compares member bytes inside the archived Linux x86_64 OrbbecSDK packages. All five drops carry the same marketing version `2.9.0`, so the package build date and SHA are part of each record's scope.

| Drop | EGO example members | Change from prior archived drop |
| --- | ---: | --- |
| 2026-09-10 | 8 | Baseline |
| 2026-09-17 | 9 | Calibration YAML export example added; two existing members changed |
| 2026-09-21 | 14 | SD card service, magnetometer, magnetic encoder and network depth example members added; four changed |
| 2026-09-23 | 15 | Network UID/hotplug example added; CMake changed |
| 2026-09-29 | 16 | Multi-device sync example added; CMake and SD card service example changed |

The supplied changelog says UMI Finger magnetometer was added on 2026-09-21 and removed on 2026-09-23, and that EgoDepth was renamed EGO-D3B on 2026-09-23. It also lists EGO wrist primary/secondary mode on 2026-09-29. These are release-note statements, not tested device/firmware compatibility. A source member remaining in a later package does not override a later removal notice for a product.

The current 15 usage records cover the 2026-09-29 host build, the SD card API sample in the 2026-09-21 and 2026-09-23 host builds, and the 2026-09-09 device-side library. Other earlier-build questions need their own source-checked records before receiving a positive API conclusion. Each new package must be archived, diffed, reviewed and replayed before replacing the catalog.
