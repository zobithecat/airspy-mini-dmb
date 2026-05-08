# eti-cmdline patches

`eti-cmdline-macos-arm64.patch` is the cumulative diff applied to
[`JvanKatwijk/eti-stuff`](https://github.com/JvanKatwijk/eti-stuff)
on top of commit at clone time. Apply with:

```sh
git clone --depth 1 https://github.com/JvanKatwijk/eti-stuff.git
cd eti-stuff
git apply ../patches/eti-cmdline-macos-arm64.patch
```

## Bug fixes

| # | File | Description |
|---|---|---|
| 1 | `eti-cmdline/CMakeLists.txt` | `AIRSPYLIB_INCLUDE_DIR` (undefined) → `LIBAIRSPY_INCLUDE_DIR` |
| 2 | `eti-cmdline/CMakeLists.txt` | `aarch64\|arm64` for Apple Silicon `-mcpu=native` |
| 3 | `eti-cmdline/includes/eti-handling/mm_malloc.h` | macOS branch: `posix_memalign` (no `<malloc.h>`) |
| 4 | `eti-cmdline/devices/airspy-handler/airspy-handler.cpp` | dylib paths for macOS (`libusb-1.0.dylib`, `libairspy.dylib`) |
| 5 | `eti-cmdline/devices/airspy-handler/airspy-handler.cpp` | **CRITICAL** — `gain * 21 / 100` bug: user-supplied gain was being divided by ~5. Now uses simplified sensitivity gain directly, plus an AGC mode (`-G 0`) that enables hardware LNA + mixer AGC |
| 6 | `eti-cmdline/eti-class.cpp` | Phase-sync thresholds relaxed `2,5` → `1,3` for weak T-DMB signals |
| 7 | `eti-cmdline/src/eti-handling/eep-protection.cpp` | **CRITICAL** — `int16_t i` overflow in `for (i = 0; i < outSize*4 + 24; i++)`: hangs forever for sub-channels ≥152 kbps. Changed to `int32_t` |
| 8 | `eti-cmdline/src/eti-handling/uep-protection.cpp` | Same overflow as #7 |
| 9 | `eti-cmdline/src/eti-handling/eti-generator.cpp` | `startProcessing()` no longer resets `amount` — preserves the 15-CIF interleave buffer that was filled while waiting for FIC |
| 10 | `eti-cmdline/src/eti-handling/viterbi-spiral/viterbi-spiral.cpp` | Buffer doubling on macOS path (Windows already had this; the comment in the source warns "code crashes without it") |
| 11 | `eti-cmdline/src/support/band-handler.cpp` | Added Korean T-DMB raster (`K7A`–`K13C`, 1.728 MHz spacing inside each 6-MHz TV channel) |

## Verifying the patches landed

```sh
DYLD_LIBRARY_PATH=/opt/homebrew/lib \
  ./eti-stuff/eti-cmdline/build/eti-cmdline-airspy \
  -C K8B -G 0 -d 60 -D 60 -t 60 -O capture.eti -J
```

With a working VHF Band III antenna near a Seoul transmitter, this should
print "ensemble YTN DMB detected" and write a non-empty ETI capture.
