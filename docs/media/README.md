# NavoX interface walkthrough

The walkthrough uses actual UI captures of the public example workspace at
[navox.net](https://navox.net/), captured on October 4, 2026 (UTC). The visible
tasks, sender names, deadlines, and search results are authored sample data from
that public example. No signed-in workspace, real inbox, account token, or
private calendar appears in these assets.

## Watch

- [48-second captioned walkthrough (MP4)](navox-walkthrough.mp4)
- [Animated README preview (GIF)](navox-walkthrough.gif)
- [Original Today screenshot](navox-interface.jpg)

| Chapter | Time | What the capture shows |
| --- | --- | --- |
| Today | 0:00–0:09 | Sample messages, meetings, and commitments in one list |
| Source context | 0:09–0:18 | Expanded sample item with its original request and deadline |
| Upcoming | 0:18–0:26 | Future sample meetings and commitments |
| Waiting | 0:26–0:33 | A sample item awaiting a reply |
| Search | 0:33–0:42 | Results for “feedback” from the sample Today and Waiting lists |
| Explore | 0:42–0:48 | The NavoX public landing page and an invitation to try the example |

This is an edited guided sequence of screenshots. Crops, explanatory captions,
brief crossfades, and a progress bar are added around the captured interface;
the UI contents are not fabricated or replaced. The persistent **Public example
• Sample data** label identifies its scope. There is no audio track.

The walkthrough demonstrates the example interface. It does not demonstrate
live Gmail sending, connected-account synchronization, authenticated search,
LLM output, approval execution, speech recognition, or text-to-speech. Those
features must be assessed separately using the implementation and appropriate
test or deployment evidence.

## Rebuild

Requirements: Python 3.10 or later, [Pillow](https://pillow.readthedocs.io/),
`ffmpeg` with `libx264`, and DejaVu fonts. On systems where the fonts are installed
elsewhere, update `FONT_DIR` in the script.

```sh
python -m pip install Pillow
python docs/media/build-walkthrough.py
```

The unmodified source JPEGs are in [`captures/`](captures/). The script encodes
the 1600×900 MP4 at 20 fps with H.264, YUV420p, and fast-start metadata. It also
creates a 960-pixel-wide looping GIF and chapter preview images. Rebuilding
requires no credentials or browser session.
