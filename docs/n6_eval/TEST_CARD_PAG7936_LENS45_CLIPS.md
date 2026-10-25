# Test card: PAG7936 + 4.5 mm lens video clips (Nick, chat 10/24 ~7:45 PM: "Please collect a sweep of videos at 30fps and 15fps, 5 sec each, at HD and 960x600 and VGA with the current setup so I can see the files.")

Pre-registered before it runs. Same setup as TEST_CARD_PAG7936_LENS45 (PAG7936, 4.5 mm lens, V3 card at ~1 m in air, evening light, auto 3A).
Runs after that card's sweep has finished (the board can stream to one reader at a time).

- **Clips:** {HD 1280×800, 960×600, VGA 640×400} × {30, 15 fps asked} × 5 s = 6 clips, N6 alone, JPEG q70 (the stream quality used in every bench),
  no tag detection. Command per clip: `scripts/sync_video.py --cameras openmv_n6 --n6-serial 020023000450433547373200 --seconds 5
  --n6-framesize <size> --board-fps <fps> --board-quality 70 --timing --meta --out ~/n6_eval/lens45_clips/<size>_<fps>fps`.
- **Raw evidence:** each clip's `openmv_n6.mjpeg` + `openmv_n6_frames.csv` + `video_sync.json` (sha256), copied unchanged to
  `~/NereusData/nereus-camera-test-rig/2026-10-24_pag7936_lens45/clips/`.
- **For viewing:** an H.264 MP4 per clip made on the Mac with ffmpeg at the clip's delivered frame rate (a derived file; the MJPEG stays the evidence).
- **Checked per clip:** rc 0, frame count ≈ fps × 5 s, delivered fps, lost / short frames, frame size, the MP4 plays (ffprobe frame count = MJPEG frames).
- Stop rules: the N6 drops off USB twice → stop, tell Nick. No board writes. Nothing on nereus002.
- Cost: ~3 min on nereus001.
