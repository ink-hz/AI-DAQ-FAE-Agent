# SDK build changes — updated 2026-10-19

2026-10-19 follow-up: a real Dev question exposed that the Linux BLE linking and runtime path answer was missing despite the archived package containing `LINUX_GUIDE.md`, `build_linux.sh` and `run_demo.sh`. The workspace catalog now has 24 usage records and 61 exact member spans. New record `daq-sdk-ble-linux-link-024` binds the 20260929 Linux x64 package's guide and scripts; it explains the sample build's link flags, RPATH and `LD_LIBRARY_PATH` without claiming tested device compatibility. This is a Dev candidate until the new catalog SHA passes model replay and independent review. Check production `/health` for its actual active release identity.

2026-10-16 intake follow-up: the [143-file coverage review](../docs/reviews/2026-10-16-daq-sdk-material-coverage.md) audited all original hashes and exact duplicate groups, including 22 versioned change logs and two unverified shortcut files. That day's **Dev candidate** had 22 records from 8 exact packages and 55 source spans: one Host C++ network UID hot-plug example, three Android Java API records, two Windows/Linux x64 BLE package integration records, and one iPhoneOS framework integration record were added. The published release then had 15 records pending Dev replay and independent answer review. The 2026-09-21 UMI Finger magnetometer addition and 2026-09-23 removal are held as a conflict despite a retained later sample. ARM64/macOS variants, AAR and other binaries remain inventory-only until separately source checked.

The separate **Dev-candidate release-note catalog** covers all 22 change-log paths as 17 unique file-hash statements. Five Linux/Windows desktop Viewer pairs share bytes and do not count as independent corroboration. The Android Viewer `v1.0.11` build 2026092302 note cites the network enumeration restriction at actual file line 8; build 2026092901's top-level entry does not declare restoration. Both records retain their exact source hashes and build identity. A release note says what a particular log states; it does not certify a binary, device pairing or field behavior. Publication must bind the note catalog SHA and an independent Dev answer review.

`update_audit.json` compares member bytes inside the archived Linux x86_64 OrbbecSDK packages. All five drops carry the same marketing version `2.9.0`, so the package build date and SHA are part of each record's scope.

| Drop | EGO example members | Change from prior archived drop |
| --- | ---: | --- |
| 2026-09-10 | 8 | Baseline |
| 2026-09-17 | 9 | Calibration YAML export example added; two existing members changed |
| 2026-09-21 | 14 | SD card service, magnetometer, magnetic encoder and network depth example members added; four changed |
| 2026-09-23 | 15 | Network UID/hotplug example added; CMake changed |
| 2026-09-29 | 16 | Multi-device sync example added; CMake and SD card service example changed |

The supplied changelog says UMI Finger magnetometer was added on 2026-09-21 and removed on 2026-09-23, and that EgoDepth was renamed EGO-D3B on 2026-09-23. It also lists EGO wrist primary/secondary mode on 2026-09-29. These are release-note statements, not tested device/firmware compatibility. A source member remaining in a later package does not override a later removal notice for a product.

The original 15 usage records covered the 2026-09-29 host build, the SD card API sample in the 2026-09-21 and 2026-09-23 host builds, and the 2026-09-09 device-side library. Other earlier-build questions need their own source-checked records before receiving a positive API conclusion. Each new package must be archived, diffed, reviewed and replayed before replacing the catalog.
