# Icon asset

The user supplied `icon.webp` and requested removal of its white background for
both the Firefox extension and desktop app. The built-in Image Generation tool
performed the background-extraction edit. The selected transparent PNG was copied
into the project and downscaled with macOS sips for packaging, preserving alpha.

- Desktop: `packaging/org.focusblocker.App.png` (512 × 512 RGBA).
- Firefox: `firefox/icons/icon-{16,32,48,96,128}.png`.
- Source image remains unchanged as `icon.webp`.

Prompt used: “Use case: background-extraction. Edit target: the provided icon.webp.
Remove only the plain white/light-gray background, including the empty spaces
around the character and within the halo. Preserve the complete original cartoon
exactly: purple-haired character in dark pot, golden halo and hair decoration,
angled implement, water splash, black outlines, and the BONK! text. Do not redesign,
recolor, add, or remove parts of the foreground. Output a clean genuinely
transparent PNG cutout suitable as a desktop application icon, centered on a
square transparent canvas with a small transparent safety margin. Keep the full
original subject and lettering uncropped. Preserve the original interior
face/clothing highlights; they are not background.”

This was an image-generation edit, not a pixel-identical deterministic mask.
The full illustration is used at all sizes; its lettering is necessarily small
in the 16-pixel toolbar rendition. No new icon artwork needs to be supplied.
