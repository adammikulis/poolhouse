# Which macOS programs read a keychain item with no dialog (2026-11-27)

One Mac, its login keychain, temporary generic-password items under a test-only service name with random values, read by scratch
app bundles (copies of the Python 3.13 interpreter) with user interaction disabled, through the ctypes calls `keyring` 's macOS
backend makes (`python3 keychain_experiment.py <empty folder> --run`; the script is not in the repository). The lead session ran
it and reported these results. Every test item was confirmed gone afterwards (`security find-generic-password` exit 44). `0` is
success, `-25293` a refused read.

| Case | Result |
|---|---|
| Ad hoc build A adds an item and reads it | 0 |
| The same build A moved to another path reads it | 0 |
| Rebuilt ad hoc build B reads A's item | -25293 |
| Certificate-signed build C1 adds an item and reads it | 0 |
| Rebuilt build C2, same certificate, reads C1's item | -25293 |
| Certificate-signed C1 reads an item an ad hoc build added | -25293 |
| Ad hoc A reads an item a certificate-signed build added | -25293 |
| Hand-off A to B, C1 to C2, A to C1, C1 to A: the old build reads its item, the new one adds its own under a new account and reads it, also after being moved, and the old item stays readable by the old build | 0, no dialog |
| B deletes A's item and still reads its own | 0 |

Not covered by the measurement: the item an installed Poolhouse already holds, a process started by launchd, a locked keychain,
and whether `poolhouse-security unlock` answered with the allow-always button restores access.
