# Airspy Mini · Korean T-DMB receiver

Airspy Mini로 한국 T-DMB(DAB Mode I + MPEG-4) 신호를 수신·복조·파싱하는
Python 패키지. RF 캡처와 OFDM 복조는 패치된
[`eti-cmdline-airspy`](https://github.com/JvanKatwijk/eti-stuff)에 위임하고,
ETI 프레임 파싱·FIC 디코드·MSC 추출·외부 FEC·TS 분석은 모두 Python.

## Stage 진행

| Stage | 상태 | 설명 |
|---|---|---|
| **1** | ✅ 완료 | ETI 파싱 + FIC 디코드 → ensemble / 서비스 / 서브채널 |
| **2** | ✅ 완료 (RS 87.3% on FIB-100% capture) | RS(204,188) + Forney 12×17 컨볼루셔널 디인터리버 |
| **3** | ✅ 완료 — 비디오 디코드 확인 | T-DMB MPEG-4 SL 디먹스 + H.264 ES + AVCC 추출 + ffmpeg 재생 |

### 1번 단계 검증
실제 K8B(183.008 MHz) 신노현 수신 → **YTN DMB ensemble (EId 0xE040)**.
FIB 100%, SNR 13 dB로 디코드 (`data/captures/k8b_100pct.eti`, 30 MB).
서비스 5개를 정확히 디코드:

```
SId 0xF1E00400  mYTN          → SubCh 1   352 kbps EEP-3A (video, MPEG-4 SL)
SId 0xF1E00402  HD mYTN       → SubCh 6   480 kbps EEP-3B
SId 0xF1E00404  4DRIVE        → packet mode (data)
SId 0xF1E00408  LOTTE Homeshop→ SubCh 9   384 kbps EEP-3B
SId 0xF1E77404  YTN EWS       → SubCh 58  FIDC (긴급경보)
```

ETI 캡처 23 MB에서 1254 frames, FIB OK 50–79% (SNR 9 dB).

### 2번 단계 검증
- 합성 클린 데이터: 200 패킷 인 → 189 패킷 아웃, RS 성공 200/200 ✓
- **실제 RF (k8b_100pct.eti, FIB 100%): RS 성공 22,652/25,953 = 87.3% ✓**
  - 이전 버전 0% 실패 → Eo & Bahk 2024 인사이트 반영:
    *"the deinterleaver requires the sync byte of a TS packet to be the
    first in an input byte chunk."* `tdmb.fec.outer` 가 0x47 위상(=offset
    160 within 204-cycle) 을 먼저 찾고, 거기서부터 deinterleaver 를 피드.
- 결론: 파이프라인 정확. FIB 70%+ 캡처라면 비디오 디코드 가능.

### 3번 단계 검증 — 비디오 디코드 성공
한국 T-DMB 비디오는 5겹으로 wrapping됨:

```
H.264 NAL → MPEG-4 SL packet (header 9B) → MPEG-2 PES (stream_id=0xFA)
         → MPEG-2 TS (PID 0x113, 188B)   → RS(204,188)+TI 외부 FEC
         → MSC sub-channel
```

`k8b_100pct.eti` (30 MB, FIB 100%) 로 end-to-end 디코드 확인:

| 단계 | 결과 |
|---|---|
| RS+TI 외부 FEC | 22,652 / 25,953 blocks = 87.3% 성공 |
| TS 패킷 | 클린 22,652개 (PID 0x113=video, 0x114=audio, 0x111/0x112=OD/SD) |
| SL→NAL 추출 | 3,135 PES → 3,135 NAL (실패 0): non-IDR 3,038 / IDR 97 |
| AVCC (SPS+PPS) | SD/BIFS stream (PID 0x112) 에서 추출 |
| ffmpeg 디코드 | **320×240 QVGA, H.264 Baseline L1.3, 25fps, YTN DMB 컨텐츠 ✓** |

SL 패킷 헤더 (한국 T-DMB 프로파일, AU-start 시):
```
Byte 0     : 0xCF 고정 (au_start=1, au_end=1, padding=0,
                       rand_acc=0, dts_flag=1, cts_flag=1 + 2 bits CTS hi)
Bytes 0..8 : 6-bit flags + 33-bit DTS + 33-bit CTS = 72 bits = 9 bytes 정확히
Bytes 9..N : ONE H.264 NAL unit (no start code prefix, NAL header 첫 바이트)
```

AVCDecoderConfigurationRecord는 OD stream 이 아니라 **SD/BIFS stream
(PID 0x112)** 에 임베드됨 (Korean T-DMB-specific):
```
01 42 00 0d ff e1 00 09 <SPS 9B> 01 00 04 <PPS 4B>
   ^^profile (Baseline)
      ^^level (1.3)
```

`tools/decode_video.py` 가 위 전체 파이프라인을 한 번에 실행:
```sh
python tools/decode_video.py data/captures/k8b_100pct.eti \
  --subch 1 --out-h264 /tmp/video.h264 --out-mp4 /tmp/video.mp4 \
  --idr-thumbnails /tmp/idr   # IDR 프레임 PNG 추출
```

남은 비트에러 (13% RS 실패) 는 ffmpeg의 error concealment 가 처리.
SNR 16 dB+ 캡처라면 100% RS 성공 → 깨끗한 비디오 가능.

## 빌드 / 설치

### 시스템 의존성 (macOS)
```sh
brew install fftw libsndfile libsamplerate pkg-config libusb airspy ffmpeg
```

### eti-cmdline-airspy (패치 적용 빌드)
```sh
git clone --depth 1 https://github.com/JvanKatwijk/eti-stuff.git
cd eti-stuff && git apply ../patches/eti-cmdline-macos-arm64.patch && cd ..
cd eti-stuff/eti-cmdline && mkdir -p build && cd build
cmake .. -DAIRSPY=ON -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
         -DCMAKE_PREFIX_PATH=/opt/homebrew
make -j4
```
패치 11종은 [patches/README.md](patches/README.md) 참조 — `int16_t`
오버플로우와 `gain * 21 / 100` 버그가 핵심.

### Python
```sh
pip install -e .            # 또는: pip install PyQt6 numpy av reedsolo
```

## 사용

### 🎬 한 줄 데모 (캡처 파일에서 전체 파이프라인 시연)
```sh
bash tools/demo.sh
```
저장된 K8B 캡처 (FIB 72%) 로 ETI → FIC → MSC → TS → 합성 PMT → ffmpeg
인식까지 단계별 출력. 신호 SNR 충분하면 같은 파이프라인이 비디오까지 디코드.

### GUI
```sh
python -m tdmb
```

### CLI 캡처
```sh
DYLD_LIBRARY_PATH=/opt/homebrew/lib \
  ./eti-stuff/eti-cmdline/build/eti-cmdline-airspy \
  -C K8B -G 0 -d 60 -D 60 -t 60 -O capture.eti -J
```
**중요:** `-C K8B` (K-prefix). 패치된 한국 raster를 쓰려면 `K` 필수.
그냥 `-C 8B`는 ETSI 표준 raster(197.648 MHz)로 튜닝되어 한국 K8B(183.008 MHz)
신호를 놓침.
- `-G 0` = 하드웨어 AGC (LNA + mixer auto)
- `-d` = OFDM 시간 동기 타임아웃(초)
- `-D` = freq sync / ensemble dump 시간

### ETI 분석 도구
```sh
python tools/dump_fic.py capture.eti              # ensemble + services
python tools/extract_ts_rs.py capture.eti --subch 1 --out clean.ts
                                                  # sync-aligned RS+TI → 클린 188B TS
python tools/extract_ts_direct.py capture.eti --subch 1 --out video.ts
                                                  # RS 우회: 204-byte 슬롯에서 188 TS 직접
python tools/extract_sl_h264_v2.py clean.ts --video-pid 0x113 --out video.h264
                                                  # SL 헤더 9B 제거 → H.264 NAL ES
python tools/extract_avcc_from_sd.py clean.ts \
       --sd-pid 0x112 --h264-in video.h264 --out video_full.h264
                                                  # SD/BIFS에서 SPS+PPS 찾아 prepend
python tools/decode_video.py capture.eti --subch 1 \
       --out-mp4 video.mp4 --idr-thumbnails /tmp/idr
                                                  # 위 모두 합친 end-to-end 파이프라인
python tools/verify_outer_fec.py capture.eti 1    # RS+TI 성공률 검증
python tools/scan.py                              # 한국 21채널 풀스캔
python tools/band_sweep.py                        # Band III 광대역 PSD 스윕
python tools/iq_analyze.py iq.raw --fs 6000000    # raw I/Q PSD/null/ADC 분석
python tools/gain_sweep.py --channel K12C         # R820T2 L/M/V 게인 매트릭스
```

### 한국 T-DMB 채널 (수도권 송출)

| 채널 | 주파수 | 사업자 |
|---|---|---|
| K8B  | 183.008 MHz | YTN DMB |
| K12A | 205.280 MHz | MBC DMB |
| K12B | 207.008 MHz | U-KBS  |
| K12C | 208.736 MHz | SBS u  |

ETSI 표준 채널은 1.712 MHz 간격이지만, 한국은 6 MHz TV 채널을
1.728 MHz 간격으로 3등분(A/B/C)하여 다른 frequency raster 사용.

## 패키지 구조

```
src/tdmb/
  channels.py       — 한국 T-DMB 주파수 테이블
  process.py        — eti-cmdline subprocess wrapper
  eti/
    frame.py        — ETI(NI) 6144B 프레임 파서
    crc.py          — CRC-16-CCITT
    fic.py          — FIB → FIG 0/0,0/1,0/2,0/14,1/x → Ensemble model
  msc.py            — MSC 서브채널 바이트 추출
  fec/
    interleaver.py  — Forney 12×17 conv. deinterleaver
    rs.py           — Reed-Solomon (204,188) decoder (DVB params)
    outer.py        — 통합 RS+TI 파이프라인 (sync-byte aligned)
  gui/app.py        — PyQt6 메인 윈도우
tools/
  dump_fic.py            — ETI 캡처에서 ensemble 덤프
  extract_ts.py          — 레거시 RS+TI 시도 (sync 안 맞춤; 권장 X)
  extract_ts_rs.py       — sync-aligned RS+TI → 클린 188B TS ★
  extract_ts_direct.py   — RS 우회, 204-byte 슬롯의 188 TS 부분 직접 추출
  extract_sl_h264_v2.py  — SL 헤더 9B 제거 → H.264 NAL ES ★
  extract_avcc_from_sd.py— SD/BIFS stream에서 AVCDecoderConfigRecord 추출 ★
  decode_video.py        — 위 모든 단계 통합 end-to-end (ETI → H.264/MP4) ★
  verify_outer_fec.py    — RS+TI 성공률 + sync offset 진단
  inject_pmt_full.py     — 합성 PMT+IOD 삽입 (dmb-ffmpeg 인식용)
  scan.py                — 21개 한국 채널 sequential lock 시도
  band_sweep.py          — Band III 광대역 PSD 스윕 (best-bump 채널 찾기)
  iq_analyze.py          — raw I/Q PSD + DAB null + ADC fill 분석
  null_detect.py         — DAB Mode I 96 ms 주기 null 정밀 검출
  gain_sweep.py          — R820T2 L/M/V 게인 매트릭스 자동 sweep
  snr_monitor.py         — 실시간 in-band/edge SNR + DAB lock 표시기
  build_dmb_ffmpeg.sh    — dmb-oss FFmpeg (BSAC + SL OD) 빌드
patches/
  eti-cmdline-macos-arm64.patch
  airspy-handler-extras.patch — AIRSPY_RATE / LNA / MIXER / VGA env vars
```

## 알려진 사항

- 동봉 FM 텔레스코픽 안테나로는 200 MHz가 약하게 잡힘. **VHF Band III
  dipole/folded dipole**로 SNR 14+ dB 확보가 Stage 2/3 진행 전제.
  실측: AliExpress LoRa 900 MHz 안테나 → Δ +1-3 dB, K8B만 가끔 OFDM 동기.
  좀 더 광대역 안테나로 교체 → Δ +4-6 dB, K8B/K12A/K12B/K12C/K10B/K11B/K13A
  6개 채널이 OFDM 동기. K12B(U-KBS)는 `FIG2 aanwezig` 까지 도달하나 FIB CRC
  통과 못 함. 12 dB 임계까지 ~5-7 dB 부족 — 외부 LNA 또는 옥상 안테나 필요.
- 한국 T-DMB 오디오는 MPEG-4 BSAC. 일반 FFmpeg는 디코딩 못 함 — 본 프로젝트는
  Stage 3에서 [dmb-oss/FFmpeg](https://github.com/dmb-oss/FFmpeg) 의 `dev-dmb`
  브랜치 사용 (BSAC + SL OD stream + DMB mpegts 패치 7종).
  주의: `master` 브랜치는 단순 upstream FFmpeg 미러이고, 실제 패치는 `dev-dmb`
  에 있음. `tools/build_dmb_ffmpeg.sh` 가 알아서 체크아웃함.
- HD DMB (HEVC + HE-AAC v2)는 TS 레벨에서 암호화되어 있음 (TTAK.KO-07.0043/R1).
  키 없이는 디코드 불가.

## 참고 문헌 / Related work

- **S. Eo and S. Bahk**, "T-DMB Receiver Implementation based on Open-source
  Software Suite," *Proc. ICTC 2024*, IEEE, pp. 313–317.
  논문에서 GNU Radio (`gr-dab` patched) + FFmpeg (BSAC + SL 패치) 조합으로
  RTL-SDR을 사용한 T-DMB 수신기를 구현. 본 프로젝트의 Stage 1/2가 그들의
  DAB 디코더 단계와 일치하며, Stage 3는 그들의 FFmpeg 패치를 통합 사용.
  소스: <https://github.com/dmb-oss>
- ETSI TS 102 427 — DMB MPEG-2 TS streaming with RS+CI outer FEC
- ETSI TS 102 428 — DMB video service (user application spec)
- TTAK.KO-07.0026/R7 — Korean DMB Video Services
- TTAK.KO-07.0024/R2 — Korean DMB System
