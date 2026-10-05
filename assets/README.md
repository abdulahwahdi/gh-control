# Assets

| File | Used for |
| --- | --- |
| `hero.svg` | Banner at the top of the main README (1280×640). |
| `menu-mock.svg` | Illustration of the dropdown on macOS and GNOME. |

Both are hand-written SVGs with no external fonts or images, so they render
anywhere and are easy to edit in a text editor. They are illustrations, not
screenshots; replace them with real screenshots once you have some.

## Social preview

GitHub's social preview image (shown when the repository link is shared)
must be a PNG or JPG, ideally 1280×640. `hero.svg` already has that size.
Convert it and upload it under **Settings → General → Social preview**:

```sh
# librsvg
rsvg-convert -w 1280 -h 640 assets/hero.svg -o social-preview.png
# or Inkscape
inkscape assets/hero.svg --export-type=png --export-filename=social-preview.png -w 1280
```

Emoji in the SVG are drawn with the fonts of whatever opens the file, so
check the PNG looks right before uploading. The PNG doesn't need to be
committed.
