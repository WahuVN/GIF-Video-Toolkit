# Công cụ cắt video

Cắt một đoạn ngắn từ video dài, có cả **GUI** lẫn **CLI**, tiếng Việt 100%.

Dùng ffmpeg ở dưới — tự động lấy binary từ gói `imageio-ffmpeg`, không cần cài ffmpeg vào hệ thống.

## Tính năng

- Xem trước video, tua bằng thanh thời gian, phát/dừng ngay trong app
- Chọn đoạn cần cắt trực quan: kéo hai đầu phạm vi, hoặc bấm "Đặt điểm vào / Đặt điểm ra"
- Phím tắt: **I** = điểm vào, **O** = điểm ra, **Space** = phát/dừng, **←/→** = lùi/tới 0.1s, **Shift+←/→** = 1s
- Nhập "Đi đến (giây hoặc HH:MM:SS)" để tua chính xác
- Đặt nhanh độ dài 5s / 10s / 15s / 30s / 1 phút / 3 phút (tính từ điểm vào)
- Hai chế độ cắt:
  - **Sao chép luồng** (mặc định): siêu nhanh, không re-encode, không mất chất lượng. Bị giới hạn bởi keyframe nên thời điểm có thể lệch nhẹ.
  - **Tái mã hóa H.264 + AAC**: cắt chính xác từng frame, tốc độ thấp hơn. Có CRF (0–51) và `preset` của x264.
- Tùy chọn giữ tiếng / bỏ tiếng, ghi đè, mở thư mục kết quả khi xong
- Kéo thả tệp video vào cửa sổ

## Cài đặt

```powershell
cd "D:\TOOL\Tao GIF\video_cutter"
python -m pip install -r requirements.txt
```

`tkinterdnd2` là tùy chọn (để bật kéo thả). Không có gói này, công cụ vẫn chạy bình thường.

## Mở GUI

Cách nhanh: nháy đúp `run_cutter.bat`, hoặc:

```powershell
python video_cutter_gui.py
```

Quy trình thường gặp:

1. Kéo thả video vào cửa sổ (hoặc bấm **Chọn tệp…**)
2. Bấm **▶ Phát** xem qua, hoặc tua thanh thời gian
3. **I** đặt điểm vào, **O** đặt điểm ra (hoặc dùng các nút tương ứng)
4. (Tùy chọn) Bấm nút "10 giây", "30 giây"… để đặt nhanh độ dài
5. Chọn chế độ cắt:
   - "Sao chép luồng" cho video dài, muốn nhanh và giữ nguyên chất lượng
   - "Tái mã hóa H.264" khi cần cắt chính xác từng frame
6. Bấm **✂ Cắt video**

## CLI

```powershell
python video_cutter.py -i input.mp4 --start 10 --end 25 -o out.mp4
```

Cắt chính xác bằng tái mã hóa:

```powershell
python video_cutter.py -i input.mp4 --start 10 --duration 15 --mode precise --crf 20 -o out.mp4
```

Bỏ tiếng:

```powershell
python video_cutter.py -i input.mp4 --start 0 --end 30 --no-audio -o silent.mp4
```

Xem metadata video:

```powershell
python video_cutter.py -i input.mp4 --probe
```

## Toàn bộ tham số CLI

```text
-i, --input        Tệp video đầu vào
-o, --output       Tệp video đầu ra (mặc định <tên>_cut_<start>-<end>.mp4)
--start            Bắt đầu (giây hoặc HH:MM:SS)
--end              Kết thúc
--duration         Thời lượng (không dùng cùng --end)
--mode             copy | precise (mặc định copy)
--no-audio         Bỏ tiếng
--crf              Chất lượng khi precise (0..51, thấp = đẹp hơn, mặc định 20)
--preset           Tốc độ encode khi precise (ultrafast .. veryslow)
--no-overwrite     Không ghi đè
--probe            Chỉ in metadata rồi thoát
--gui              Mở GUI
```

## Mẹo

- Dùng `copy` cho cắt nhanh từ video dài. Nếu thấy đầu đoạn bị lệch, đổi sang `precise`.
- `precise` cho phép cắt chính xác từng frame nhưng tốn CPU. Dùng `--preset ultrafast` khi cần nhanh.
- CRF 18 ≈ chất lượng rất cao, 20 ≈ cao, 23 ≈ tốt, 28 ≈ chấp nhận được. Tăng để tệp nhỏ hơn.

## Cấu trúc

- `video_cutter.py`: thư viện + CLI
- `video_cutter_gui.py`: giao diện đồ họa (dùng chung `MediaSource` và `RangeSlider` từ thư mục cha của bộ GIF Tool)
- `run_cutter.bat`: chạy không tham số sẽ mở GUI; có tham số sẽ chạy CLI
- `requirements.txt`: phụ thuộc

## Dùng trong code

```python
from pathlib import Path
from video_cutter import CutOptions, cut_video

cut_video(CutOptions(
    input_path=Path("long.mp4"),
    output_path=Path("short.mp4"),
    start=12.0,
    end=27.5,
    mode="precise",
    crf=20,
))
```
