## Optional extras with copyleft licences

These are never bundled and never installed with Poolhouse. Each is on a device only when you installed it, or when what
you asked for needs it and it was installed at first use (`poolhouse.provision`), as the table says.

| Extra | Package | Licence | Effect |
|---|---|---|---|
| `speech` | piper-tts | GPL-3.0-or-later | `poolhouse.speech.tts` imports `piper` at run time. Poolhouse does not distribute piper. Distributing a build of Poolhouse together with piper would have to satisfy the GPL for the combination. |
| `ffmpeg` (also in `audio`) | imageio-ffmpeg (BSD-2-Clause), whose wheel carries an `ffmpeg` binary built with GPL parts | the binary's own, GPL | Installed at first use only when audio has to be converted and no `ffmpeg` is on PATH. It is run as a separate program; Poolhouse does not distribute it. |
| `heif` | pillow-heif (BSD-3-Clause), whose wheels carry libheif and codec libraries | the libraries' own, LGPL and, for the HEVC encoder, GPL | Installed at first use only when a HEIC picture has to be converted. Poolhouse does not distribute it. |
| `pdf-agpl` (in neither `all` nor `redteam`) | pymupdf (MuPDF) | AGPL-3.0 or commercial | Opt-in engine only: `POOLHOUSE_PDF_ENGINE=pymupdf` makes `poolhouse.sources.pdf` and `poolhouse.net.pdftext` import `pymupdf`, and the datasheet pin tables need it (page pictures and outline pages do not). The default `pdf` extra reads with pdfminer.six (MIT) and Pillow and renders pages with PDFium through pypdfium2 (BSD-3-Clause / Apache-2.0). Offering a service built on pymupdf over a network triggers the AGPL for that service. |

Other optional extras (`torch`, `transformers`, `peft`, `mlx*`, `manim`, `spacy`, `presidio-analyzer`, `playwright`,
`lm-eval` and others) are permissively licensed at the top level, but their own dependencies are not inventoried
here; run `pip-licenses` in the environment you build from before redistributing it.

## Code ported into this repository

See `NOTICE` (qwen-spec, DFlash, mlx-lm, SGLang).

## Programs run as separate processes

llama.cpp (`llama-server`, MIT), ffmpeg, and the models you download are separate programs with their own
licences; none is bundled in the wheel or in the release bundles. The bundles are built with PyInstaller
(GPL-2.0 with the bootloader exception, which permits distributing the frozen application under any licence)
and Tauri (MIT or Apache-2.0).

The `claude` extra, which every installed runtime and installer asks for, installs `claude-agent-sdk` (MIT). Its
platform wheels bundle the Claude Code binary, which is Anthropic's software under Anthropic's terms, not under this
project's licence; it is fetched from PyPI at install time and is not bundled in the wheel or the release bundles.
