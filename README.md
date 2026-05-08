# Airspy Mini · Korean T-DMB receiver

Airspy Mini로 한국 T-DMB(DAB Mode I + MPEG-4) 신호를 수신·복조·파싱하는
Python 패키지. RF 캡처와 OFDM 복조는 패치된
[`eti-cmdline-airspy`](https://github.com/JvanKatwijk/eti-stuff)에 위임하고,
ETI 프레임 파싱·FIC 디코드·MSC 추출·외부 FEC·TS 분석은 모두 Python.

## Stage 진행

| Stage | 상태 | 설명 |
|---|---|---|
| **1** | ✅ 완료 | ETI 파싱 + FIC 디코드 → ensemble / 서비스 / 서브채널 |
| **2** | ✅ 코드 완료, RF 검증 미완 | RS(204,188) + Forney 12×17 컨볼루셔널 디인터리버 |
| **3** | ⏸ 대기 | T-DMB MPEG-4 SL 디먹스 + H.264 + BSAC 재생 |

### 1번 단계 검증
실제 K8B(183.008 MHz) 신노현 수신 → **YTN DMB ensemble (EId 0xE040)**.
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
- 실제 RF: 0/5002 RS 성공 — BER ~7%가 RS(t=8)의 정정 한계 초과
- 결론: 파이프라인 자체는 정확. 신호 SNR이 14 dB+ 되어야 통함.

### 3번 단계의 실체
한국 T-DMB 비디오는 4겹으로 wrapping됨:

```
H.264 → MPEG-4 SL packet → MPEG-2 PES → MPEG-2 TS (188B) → MSC sub-channel
```

추출한 PES에서 `stream_id=0xfa` (SL-packetized) 확인. 일반 ffmpeg는
이걸 풀 수 없고 (NAL start code도 안 나옴), 별도 SL/OD 디먹서가 필요.

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
- `-G 0` = 하드웨어 AGC (LNA + mixer auto)
- `-d` = OFDM 시간 동기 타임아웃(초)
- `-D` = freq sync / ensemble dump 시간

### ETI 분석 도구
```sh
python tools/dump_fic.py capture.eti              # ensemble + services
python tools/extract_ts.py capture.eti --subch 1  # RS+TI 시도
python tools/extract_ts_direct.py capture.eti --subch 1 --out video.ts
                                                  # 204-byte 슬롯에서 188 TS 직접 추출
python tools/scan.py                              # 한국 21채널 풀스캔
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
    outer.py        — 통합 RS+TI 파이프라인
  gui/app.py        — PyQt6 메인 윈도우
tools/
  dump_fic.py       — ETI 캡처에서 ensemble 덤프
  extract_ts.py     — RS+TI 디코더로 TS 패킷 추출
  extract_ts_direct.py — RS 우회, 204-byte 슬롯의 188 TS 부분 직접 추출
  scan.py           — 21개 한국 채널 sequential lock 시도
patches/
  eti-cmdline-macos-arm64.patch
```

## 알려진 사항 / TODO

- 동봉 FM 텔레스코픽 안테나로는 200 MHz가 약하게 잡힘. **VHF Band III
  dipole/folded dipole**로 SNR 14+ dB 확보가 Stage 2/3 진행 전제.
- Stage 3 SL/OD 디먹서 자체 구현은 1–2주 작업. 현실적 대안:
  - [`welle.io`](https://www.welle.io/) — T-DMB 비디오 통합 검증된 도구
  - 본 프로젝트는 ETI까지의 파이프라인이 강점이므로 SL→H.264는
    ffmpeg patch + custom demuxer 또는 welle.io 결합으로 가는 게 실용적.
- 오디오: 한국 T-DMB는 MPEG-4 BSAC. FFmpeg BSAC 디코더는 experimental.
