# Upstream PR plan for eti-stuff

이 문서는 [JvanKatwijk/eti-stuff](https://github.com/JvanKatwijk/eti-stuff)
에 보낼 PR을 준비하기 위한 메모. 11개 패치를 성격에 따라 묶어서 별도 PR로
보내는 게 review 받기 쉬움.

## PR 1 — Critical bugs (모든 플랫폼 공통)

가장 중요. 이 둘은 macOS만의 문제가 아니라 모든 플랫폼에서 ≥152 kbps
서브채널을 처리할 때 발생하는 실제 버그.

### Bug A — `int16_t` overflow in protection layers

**Files:** `src/eti-handling/eep-protection.cpp`, `src/eti-handling/uep-protection.cpp`

`outSize * 4 + 24` (예: 352 kbps EEP-3A → 33840) 이 `int16_t` max 32767을
초과해서 `for (i = 0; i < outSize*4 + 24; i++)` 가 무한 루프.

```diff
- int16_t i;
- int16_t inputCounter = 0;
+ int32_t i;        // was int16_t — overflows for ≥152 kbps sub-channels
+ int32_t inputCounter = 0;
```

같은 파일 생성자 안 `viterbiCounter`도 같은 문제 있음.

### Bug B — `gain * 21 / 100` silent gain reduction

**File:** `devices/airspy-handler/airspy-handler.cpp`

`-G 14` 사용자 입력이 `set_sensitivity_gain(device, 14*21/100)` = 2 (max 21
중 2)로 내부 변환. 사용자가 의도한 게인의 1/5만 적용되며 약 신호에서
실제로 lock 못 잡는 원인.

```diff
-result = my_airspy_set_sensitivity_gain(device, gain * 21 / 100);
+// `gain` is already the simplified sensitivity index (0..21); pass directly.
+result = my_airspy_set_sensitivity_gain(device, gain);
```

(부가: `gain == 0` 일 때 LNA+mixer AGC 모드 추가는 별도 PR로 분리 권장.)

### Bug C — viterbiSpiral buffer doubling on non-Windows

**File:** `src/eti-handling/viterbi-spiral/viterbi-spiral.cpp`

소스 주석:
> "BIG NOTE: The spiral code uses (wordLength + (K - 1)) * sizeof ...
> However, the application then crashes, so something is not OK
> By doubling the size, the problem disappears."

Windows 경로에는 doubling이 적용되어 있지만, POSIX 경로
(`posix_memalign`)에는 빠져 있음. 추가:

```diff
 if (posix_memalign((void**)&data, 16,
-                   (wordlength + (K - 1)) / 8 + 1)) { ... }
+                   2 * ((wordlength + (K - 1)) / 8 + 1 + 16))) { ... }
```

`symbols`, `vp.decisions`도 동일하게 doubling.

---

## PR 2 — macOS portability

자가 완결, 다른 플랫폼 영향 없음. 단일 PR로 묶기 좋음.

### Files & changes

1. `includes/eti-handling/mm_malloc.h` — Apple branch (`posix_memalign`)
   ```c
   #if defined(_WIN32)
   #define MALLOC(a) _mm_malloc(a, 16)
   #elif defined(__APPLE__)
   #include <stdlib.h>
   static inline void *_aligned_malloc(size_t s) {
       void *p = NULL;
       return posix_memalign(&p, 16, s) ? NULL : p;
   }
   #define MALLOC(a) _aligned_malloc(a)
   #else
   #include <malloc.h>
   #define MALLOC(a) memalign(16, a)
   #endif
   ```

2. `devices/airspy-handler/airspy-handler.cpp` — `.dylib` paths in
   `dlopen("libusb-1.0.so")` and `dlopen("libairspy.so")`.

3. `eti-cmdline/CMakeLists.txt` — `aarch64|arm64` so Apple Silicon
   (`uname -m` returns `arm64`) hits the `-mcpu=native` branch instead of
   the `-march=armv7-a` one (which clang rejects).

4. `eti-cmdline/CMakeLists.txt` — `LIBAIRSPY_INCLUDE_DIR` (correct cmake
   variable) instead of `AIRSPYLIB_INCLUDE_DIR` (undefined). Currently
   the header `libairspy/airspy.h` only resolves because `/opt/homebrew/include`
   happens to be in the default path on Homebrew systems.

---

## PR 3 — Weak-signal robustness (논의 필요)

작은 동작 변경. 메인테이너 의견 필요할 수 있음.

### Phase sync thresholds

**File:** `eti-class.cpp`

```diff
-                                     2, 5,
+                                     1, 3,
```

기본값을 낮추는 건 false-lock 위험. 차라리 `-T threshold1,threshold2`
명령행 옵션 추가가 더 좋을 듯.

### `startProcessing` no-reset of `amount`

**File:** `src/eti-handling/eti-generator.cpp`

```diff
 void etiGenerator::startProcessing(void) {
     processing.store(true);
     fprintf(stderr, "yes, here we go\n");
-    amount.store(0);
+    // keep amount as-is: the 15-CIF interleave buffer is already filled
+    // while we were waiting for FIC.
 }
```

자명한 개선. PR로 보내기 좋음.

---

## PR 4 — Korean T-DMB channel raster (feature)

**File:** `src/support/band-handler.cpp`

한국 T-DMB는 1.728 MHz 간격으로 6 MHz TV 채널을 3등분. ETSI Band III
raster (1.712 MHz)와 다름.

```c
// Korean T-DMB raster (TV ch 7-13, sub A/B/C)
{"K7A", 175280}, {"K7B", 177008}, {"K7C", 178736},
{"K8A", 181280}, {"K8B", 183008}, {"K8C", 184736},
...
{"K13A", 211280}, {"K13B", 213008}, {"K13C", 214736},
```

`-C K8B` 같은 prefix로 사용. 기존 ETSI 채널과 충돌 없음.

다른 raster 사용하는 국가 (예: 일부 Asia/EMEA 국가) 도 비슷한 방식으로
추가 가능. 이게 더 일반적 접근일 수도 — `-B BAND_KOREA` 같은 별도 밴드로
분리하는 안.

---

## PR 5 — AGC mode (feature)

**File:** `devices/airspy-handler/airspy-handler.cpp`

`-G 0` 일 때 LNA+mixer AGC 활성화, VGA는 중앙값. 약 신호에서 sensitivity
gain 21로 고정한 것보다 실측 결과가 더 좋음 (전체 신호레벨에 따라 LNA가
자동 조정).

```c
if (gain <= 0) {
    if (my_airspy_set_lna_agc != NULL)   my_airspy_set_lna_agc(device, 1);
    if (my_airspy_set_mixer_agc != NULL) my_airspy_set_mixer_agc(device, 1);
    if (my_airspy_set_vga_gain != NULL)  my_airspy_set_vga_gain(device, 10);
} else {
    /* sensitivity gain path */
}
```

별도 PR로 분리 권장. 기존 `gain` 값이 0 이하인 경우의 동작이 명확하지
않으므로 (현재는 `0 * 21 / 100 = 0` 으로 최저 게인) 사용자 영향 검토 필요.

---

## 보내는 순서 권장

1. **PR 1** 먼저 — 기능 회귀 없는 명확한 버그 수정. 수락 가능성 높음.
2. **PR 2** — macOS 빌드 enabling. 독립적, 다른 플랫폼 무영향.
3. **PR 3** — `startProcessing` 개선만 보내고, threshold 변경은 옵션으로.
4. **PR 4** — Korean raster.
5. **PR 5** — AGC mode (마지막).

각 PR에 본 프로젝트(airspy-mini-dmb) 링크와 한국 T-DMB 수신 시연 결과
첨부하면 review 도움 됨.
