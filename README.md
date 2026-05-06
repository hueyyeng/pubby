# pubby

A PySide6 UI mockup of a typical media ingestor tool.

This requires `ffmpeg`, `ffprobe` and `Rclone` for external dependencies.

> Disclaimer: The Dry Run mode is working but can choke on many files? Majority of the code is done through
> "instinct" and "lots of whipping" of Cl and Qw models.

## Why the mockup?

1. The core idea is that copying using `shutil.copy2` is slow and robocopy is Windows only. Also Rclone rocks.
2. My users... uses Windows/File Explorer to do "publishing". Bad UX.
3. Also said users uses FreeFileSync but ended up abandoning it for ye good olde Windows/File Explorer.
4. There is Pomfort Offload Manager and Hedge Offshoot but we're budget constraint.
5. Finally, we have a very specific way of selecting a show and start offloading all the footages hence this mockup.

This repository is kept simple and uses PySide6 so it should sit right at home with most VFX/Game pipeline with the
right amount of tweaking.

I definitely will not use this repo as it but more of a proof of concept to show to stakeholders that it is doable to
create a bespoke custom media ingestor tool without relying on a commercial solution.
