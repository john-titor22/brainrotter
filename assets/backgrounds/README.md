# Background footage

Drop looping gameplay / satisfying clips here, one folder per **category**:

```
assets/backgrounds/
  subway/        subway_surfers_01.mp4  ...
  parkour/       minecraft_parkour_*.mp4
  satisfying/    hydraulic_press_*.mp4
  gta/           gta_ramps_*.mp4
```

- Vertical (9:16) or it'll be center-cropped. 1080×1920 ideal.
- 30–60 s clips are plenty; the engine loops/trims to match narration length.
- `.mp4 .mkv .webm .mov .m4v` are picked up. Loose files directly in this folder
  go into an "uncategorized" bucket.

The Director asks `assets.pick(category)` for a category; leaving it unset lets
Brainrotter choose from everything available. `brainrotter doctor` warns if this
folder is empty.

`testpattern/` holds a generated ffmpeg test clip so the pipeline runs before you
add real footage — safe to delete once you have your own.
