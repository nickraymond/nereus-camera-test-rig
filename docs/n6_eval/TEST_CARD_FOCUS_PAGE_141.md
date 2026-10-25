# Test card: focus page resolution switch + live sensor (PR #141) on the N6 (Nick, chat 10/24 ~7:00 PM: "Lets kill the open stream and test the new features live now")

Pre-registered before it runs. A live functional check, not a measurement.

- **Code:** branch `test/141-on-n6-eval` = feat/n6-eval + PR #141's commit 51beb01, cherry-picked. Conflict in `capture_service.py` resolved by keeping
  both blocks (today's meta / tag fields, then #141's `sensor_id` on frame 0). #141 itself is based on main, so deploying it as-is would drop
  feat/n6-eval's stream options and the PS5520 sizes. The test branch keeps them.
- **Board change:** `capture_service.py` only. Back up the 7 board files first, deploy with `host_tools/deploy_openmv.py`, then `board_hashes` ALL MATCH
  and an HD still `completed`. Rollback: redeploy fda23ad (feat/n6-eval) or copy the backup back.
- **Run:** `python -m host_tools.focus_stream --serial 020023000450433547373200 --framesize HD --quality 70 --http-port 8096
  --framesizes QVGA,VGA,960x600,HD,1280x960,1920x1080,2560x1440,2592x1944 --max-seconds 3600` on nereus001, page at http://nereus001:8096.
- **Pass:** the page shows sensor "PS5520 · 5 MP"; switching to each of the 8 sizes gives a live picture whose reported size matches; it switches back to HD.
  Any size that fails to stream is reported, not hidden.
- **Stop:** the N6 drops off USB twice → stop, tell Nick. Nothing on nereus002, no firmware change.
