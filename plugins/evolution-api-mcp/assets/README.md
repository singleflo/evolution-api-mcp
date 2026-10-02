# assets/

`icon.png` (composer icon) and `logo.png` are rendered by
`scripts/make_icon.py` (Pillow, 512x512, flat background, the letters "EA"),
which also writes `docs/listing/icon.png`. Re-run `uv run python scripts/make_icon.py`
to regenerate all three; the output is deterministic. The mark is plain text on a
flat field: no WhatsApp or Evolution API artwork is used.
