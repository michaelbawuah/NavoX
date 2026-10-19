# Local own-work PDF renderer

The exact dependency is `reportlab==5.0.1`, licensed under the BSD license.
Its wheel and source archive checksums are pinned in `services/api/uv.lock`.
Version and license were verified against the official project release:
https://pypi.org/project/reportlab/5.0.1/

Only the documented Canvas text primitives, `TTFont`, and an in-memory binary
stream are used. There is no HTML, RML, image, URL, or remote font renderer.
Official reference:
https://docs.reportlab.com/reportlab/userguide/ch2_graphics/
https://docs.reportlab.com/reportlab/userguide/ch3_fonts/
https://docs.reportlab.com/releases/notes/whats-new-50/

The unmodified DejaVu Sans Book and Bold 2.37 font files include their complete
embedded distribution license in `fonts/LICENSE.txt`. `fonts/PROVENANCE.json`
records the official source, license URL and exact asset checksums. The renderer
verifies the font bytes and library version in each fresh worker process.
Official version and license references:
https://dejavu-fonts.github.io/
https://dejavu-fonts.github.io/License.html

The verified initial profile is straightforward left-to-right text with available
precomposed glyphs, including the Latin, Greek, Cyrillic and mathematical examples
covered by tests. Missing glyphs, combining sequences and unverified right-to-left
shaping return a bounded error. This is not a promise of support for all Unicode
or all writing systems. Line endings are displayed as line breaks; layout adds
wrapping, the explicitly approved title page, repeated title and page numbers.

The fresh worker receives only the validated own text and confirmed metadata over
stdin. It uses CPU, address-space, file-size and wall-clock limits, a bounded PDF
buffer, a two-slot per-API-process semaphore, no inherited credentials, and an
audit guard against network transport, subprocesses and filesystem mutations.
These are application resource and capability boundaries, not a claim that a
Python audit hook replaces an operating-system sandbox. Student content is not
stored in a database, temporary file, model context or history.
