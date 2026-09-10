# assets/movies/

Drop a movie file here (`.mp4`, `.mkv`, `.mov`, `.webm`, `.avi`) and Brainrotter
can cut it into a **movie-recap series** — a whole film in shorts, muted, with a
brainrot narrator over the top.

```
assets/movies/The Matrix.mp4
```

then

```
brainrotter run -f movie_recap -t "the matrix" --series
```

The filename is matched loosely against the topic, and quality tags
(`1080p`, `x265`, a trailing year…) are stripped for the on-screen title.

## What happens per part

1. The film is split by time into Part 1..N (`[movie] movie_seconds_per_part`).
2. Each part extracts its chunk, transcribes the dialogue locally with
   `faster-whisper` (model auto-downloads once, ~150 MB).
3. The LLM writes a fast recap narration from the transcript.
4. The chunk's shots are scene-detected, cut into a silent 9:16 montage.
5. Narration + word-pop captions + quiet music are stacked over the montage.

## Note

A movie is someone's copyright. The recap/commentary format is widespread and
sits in a grey area; sourcing and use are your responsibility. Files here are
gitignored.
